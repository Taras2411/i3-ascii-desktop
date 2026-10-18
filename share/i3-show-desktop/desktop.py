#!/usr/bin/env python3
"""Toggle empty i3 workspaces on every output without moving application windows."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_common.i18n import tr, get_language
import argparse
import fcntl
import json
import os
import subprocess
import uuid


def quote(value):
    # Pass one i3 command argument; keep Unicode intact (i3 is not a JSON parser).
    return json.dumps(str(value),ensure_ascii=False)


def walk(node, workspace=None):
    if node.get('type')=='workspace':workspace=node
    yield node,workspace
    for child in node.get('nodes',[])+node.get('floating_nodes',[]):
        yield from walk(child,workspace)


def read_json(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return None


def write_json(path, value):
    temporary=path.with_suffix('.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(value,stream,ensure_ascii=False)
    os.replace(temporary,path)
    os.chmod(path,0o600)


class I3:
    def query(self,kind):
        return json.loads(subprocess.check_output(['i3-msg','-t',kind],text=True,timeout=5))

    def command(self,commands):
        if not commands:return
        result=json.loads(subprocess.check_output(['i3-msg','; '.join(commands)],text=True,timeout=8))
        errors=[item.get('error','i3 rejected a command') for item in result if not item.get('success')]
        if errors:raise RuntimeError('; '.join(errors))

    def session(self):
        return subprocess.check_output(['i3','--get-socketpath'],text=True,timeout=5).strip()

    def pointer(self):
        try:
            data=subprocess.check_output(['xdotool','getmouselocation','--shell'],text=True,timeout=2)
            fields=dict(line.split('=',1) for line in data.splitlines())
            return int(fields['X']),int(fields['Y'])
        except (OSError,ValueError,KeyError,subprocess.SubprocessError):return None

    def move_pointer(self,position):
        if position:
            subprocess.run(['xdotool','mousemove',str(position[0]),str(position[1])],
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=2,check=False)


def numbering_plan(workspaces, outputs, tree):
    """Number occupied workspaces in monitor columns, preserving bar order.

    Peeking needs exactly one empty workspace per active output. Reuse an
    existing empty one where possible; other empties are discarded, not counted
    as occupied slots. No padding workspaces are added between occupied slots.
    """
    nodes={n['id']:n for n,_ in walk(tree) if n.get('type')=='workspace'}
    outputs=sorted((o for o in outputs if o['active']),key=lambda o:o['rect']['x'])
    targets={};peeks=[];discard=[]
    stride=len(outputs)
    for column,output in enumerate(outputs,1):
        group=[w for w in workspaces if w['output']==output['name']]
        # get_workspaces reports i3's current per-output bar order.
        occupied=[w for w in group if any(n.get('window') for n,_ in walk(nodes.get(w['id'],{})))]
        empty=[w for w in group if w not in occupied]
        for row,workspace in enumerate(occupied):targets[workspace['id']]=str(column+row*stride)
        chosen=next((w for w in empty if w['visible']),empty[0] if empty else None)
        name=str(column+len(occupied)*stride)
        if chosen:targets[chosen['id']]=name
        peeks.append(dict(output=output['name'],name=name,id=chosen['id'] if chosen else None))
        discard.extend(w for w in empty if not chosen or w['id']!=chosen['id'])
    return targets,peeks,discard


def next_workspace(workspaces, outputs):
    """First globally unused number in the focused monitor's column."""
    outputs=sorted((o for o in outputs if o['active']),key=lambda o:o['rect']['x'])
    focused=next((w for w in workspaces if w.get('focused')),None)
    if not focused or not outputs:raise RuntimeError(tr('No active monitor found'))
    column=next((index+1 for index,o in enumerate(outputs) if o['name']==focused['output']),None)
    if column is None:raise RuntimeError(tr('Active monitor is disconnected'))
    occupied=set()
    for workspace in workspaces:
        number=workspace.get('num',-1)
        if number>=0:occupied.add(number)
        if workspace['name'].isdigit():occupied.add(int(workspace['name']))
    number=column
    while number in occupied:number+=len(outputs)
    return str(number),focused['output']


def create_workspace(i3):
    before=i3.query('get_workspaces')
    name,output=next_workspace(before,i3.query('get_outputs'))
    i3.command([f'workspace --no-auto-back-and-forth {quote(name)}'])
    created=next((w for w in i3.query('get_workspaces') if w['name']==name),None)
    if not created:raise RuntimeError(tr('Could not create a workspace'))
    if created['output']!=output:
        # A static output assignment can be stale after a monitor is unplugged.
        # Relocate only the newly created empty workspace, never an application.
        node=next((n for n,_ in walk(i3.query('get_tree')) if n.get('type')=='workspace' and n['id']==created['id']),None)
        if not node or any(n.get('window') for n,_ in walk(node)):
            raise RuntimeError(tr('An i3 rule assigned the new workspace to another monitor; windows were left in place'))
        i3.command([f'workspace --no-auto-back-and-forth {quote(name)}',
                    f'move workspace to output {quote(output)}',
                    f'workspace --no-auto-back-and-forth {quote(name)}'])
    return name


class DesktopToggle:
    def __init__(self,i3,state_path):
        self.i3,self.path=i3,state_path

    def state(self):
        value=read_json(self.path)
        if not isinstance(value,dict) or value.get('version') not in (1,2) or not isinstance(value.get('workspaces'),list) or value.get('session')!=self.i3.session():return None
        return value

    def show(self):
        workspaces=self.i3.query('get_workspaces')
        outputs=self.i3.query('get_outputs')
        tree=self.i3.query('get_tree')
        targets,peeks,discard=numbering_plan(workspaces,outputs,tree)
        visible=[dict(w) for w in workspaces if w['visible']]
        visible_ids={w['id'] for w in visible}
        nodes=list(walk(tree))
        focused=next((n for n,_ in nodes if n.get('focused')),None)
        workspace_nodes={n['id']:n for n,_ in nodes if n.get('type')=='workspace'}
        fullscreen=[{'id':n['id'],'mode':n['fullscreen_mode']} for n,w in nodes
                    if n.get('type') in ('con','floating_con') and n.get('fullscreen_mode',0)
                    and (n['fullscreen_mode']==2 or (w and w['id'] in visible_ids))]
        sticky=[n['id'] for n,_ in nodes if n.get('sticky') and n.get('type') in ('con','floating_con')]
        for saved in visible:
            saved['name']=targets.get(saved['id'],saved['name'])
            saved['layout']=workspace_nodes.get(saved['id'],{}).get('layout','splith')
        state=dict(version=2,session=self.i3.session(),workspaces=visible,
                   focused=focused['id'] if focused else None,
                   focused_workspace=next((w for w in visible if w['focused']),None),
                   pointer=self.i3.pointer(),fullscreen=fullscreen,sticky=sticky,
                   temporary=[peek['name'] for peek in peeks],numbering=targets)
        commands=[f'[con_id={item["id"]}] fullscreen disable' for item in fullscreen]
        commands += [f'[con_id={identifier}] sticky disable' for identifier in sticky]
        # Two-phase renaming avoids swaps/cycles and collisions with old empty
        # numeric names. Neither phase moves a workspace or an application.
        prefix='_desktop_rename_'+uuid.uuid4().hex
        parked={w['id']:f'{prefix}_{w["id"]}' for w in workspaces if w['id'] in targets or w in discard}
        commands += [f'rename workspace {quote(w["name"])} to {quote(parked[w["id"]])}'
                     for w in workspaces if w['id'] in parked]
        commands += [f'rename workspace {quote(parked[identifier])} to {quote(name)}'
                     for identifier,name in targets.items()]
        # Remove windowless layout placeholders, including in the reused empty
        # desktop, so it can disappear naturally after restore. Workspaces
        # containing any application window are excluded entirely.
        for workspace in workspaces:
            if workspace['id'] not in parked:continue
            node=workspace_nodes.get(workspace['id'],{})
            if any(child.get('window') for child,_ in walk(node)):continue
            for child,_ in walk(node):
                if child.get('type')=='con' and not child.get('window') and not child.get('nodes') and not child.get('floating_nodes'):
                    commands.append(f'[con_id={child["id"]}] kill')
        for peek in peeks:
            # Visit/discard extra empties by switching away; i3 removes an empty
            # workspace when it becomes hidden. The final visible name is numeric.
            for workspace in discard:
                if workspace['output']==peek['output']:
                    commands += [f'focus output {quote(peek["output"])}',
                                 f'workspace --no-auto-back-and-forth {quote(parked[workspace["id"]])}']
            commands += [f'focus output {quote(peek["output"])}',
                         f'workspace --no-auto-back-and-forth {quote(peek["name"])}']
        if state['focused_workspace']:
            commands.append(f'focus output {quote(state["focused_workspace"]["output"])}')
        write_json(self.path,state)
        try:
            self.i3.command(commands)
            self.i3.move_pointer(state['pointer'])
        except Exception:
            self.restore(state)
            raise
        return state

    def restore(self,state=None):
        state=state or self.state()
        if not state:return
        workspaces=self.i3.query('get_workspaces')
        active={o['name'] for o in self.i3.query('get_outputs') if o['active']}
        tree=self.i3.query('get_tree')
        ids={n['id'] for n,_ in walk(tree)}
        commands=[]
        for saved in state['workspaces']:
            if saved['output'] not in active:continue
            current=next((w for w in workspaces if w['id']==saved['id']),None)
            if current is None:current=next((w for w in workspaces if w['name']==saved['name']),None)
            name=current['name'] if current else saved['name']
            commands += [f'focus output {quote(saved["output"])}',
                         f'workspace --no-auto-back-and-forth {quote(name)}']
            if not current and saved.get('layout') in ('splith','splitv','stacked','tabbed'):
                commands.append('layout '+saved['layout'])
        commands += [f'[con_id={identifier}] sticky enable' for identifier in state.get('sticky',[]) if identifier in ids]
        commands += [f'[con_id={item["id"]}] fullscreen enable'+(' global' if item['mode']==2 else '')
                     for item in state.get('fullscreen',[]) if item['id'] in ids]
        self.i3.command(commands)
        self.i3.move_pointer(state.get('pointer'))
        focused_node=next((n for n,_ in walk(tree) if n['id']==state.get('focused')),None)
        if focused_node and focused_node.get('type')!='workspace':
            self.i3.command([f'[con_id={state["focused"]}] focus'])
        else:
            saved=state.get('focused_workspace')
            if saved and saved['output'] in active:
                current=next((w for w in workspaces if w['id']==saved['id']),None)
                self.i3.command([f'workspace --no-auto-back-and-forth {quote(current["name"] if current else saved["name"])}'])
        # Empty temporary workspaces vanish naturally. Workspaces where the user
        # opened a new application stay intact; never close or relocate it.
        self.path.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    actions=parser.add_mutually_exclusive_group()
    actions.add_argument('--new',action='store_true',help='Create and focus the next numeric workspace on the active output')
    actions.add_argument('--restore',action='store_true',help='Return without starting another desktop view')
    actions.add_argument('--status',action='store_true',help='Print on/off without changing i3')
    args=parser.parse_args()
    runtime=Path(os.environ.get('XDG_RUNTIME_DIR',f'/tmp/i3-desktop-{os.getuid()}'))
    runtime.mkdir(mode=0o700,parents=True,exist_ok=True)
    state_path=runtime/'i3-show-desktop.json'
    with (runtime/'i3-show-desktop.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        toggle=DesktopToggle(I3(),state_path)
        try:
            state=None if args.new else toggle.state()
            if args.new:create_workspace(toggle.i3)
            elif args.status:print('on' if state else 'off')
            elif state or args.restore:toggle.restore(state)
            else:toggle.show()
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
            print(f'i3-show-desktop: {error}',file=sys.stderr)
            subprocess.run(['notify-send',tr('i3 workspaces'),str(error)],check=False)
            return 1
    return 0


if __name__=='__main__':sys.exit(main())
