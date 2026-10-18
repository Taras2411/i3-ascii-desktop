import json
from pathlib import Path
import tempfile
import unittest
from desktop import numbering_plan, next_workspace, create_workspace, DesktopToggle, walk


def fixture(groups):
    workspaces=[];nodes=[];outputs=[];identifier=10
    for column,(output,names) in enumerate(groups.items()):
        outputs.append(dict(name=output,active=True,rect={'x':column*1000}))
        for index,(name,windows) in enumerate(names):
            identifier+=1
            workspaces.append(dict(id=identifier,name=name,output=output,num=int(name) if name.isdigit() else -1,visible=index==0,focused=column==0 and index==0))
            children=[dict(id=identifier*100+i,type='con',window=identifier*100+i,nodes=[],floating_nodes=[],focused=False) for i in range(windows)]
            nodes.append(dict(id=identifier,type='workspace',name=name,nodes=children,floating_nodes=[],layout='splith',focused=column==0 and index==0))
    return workspaces,outputs,dict(id=1,type='root',nodes=nodes,floating_nodes=[])


class NumberingTests(unittest.TestCase):
    def test_user_example_has_five_occupied_workspaces_without_padding(self):
        ws,outputs,tree=fixture({'left':[('1',1),('8',1)],'center':[('3',1)],'right':[('2',1),('7',1)]})
        targets,peeks,discard=numbering_plan(ws,outputs,tree)
        actual={(w['output'],w['name']):targets[w['id']] for w in ws}
        self.assertEqual(actual,{('left','1'):'1',('left','8'):'4',('center','3'):'2',('right','2'):'3',('right','7'):'6'})
        self.assertEqual([p['name'] for p in peeks],['7','5','9'])
        self.assertTrue(all(p['id'] is None for p in peeks));self.assertFalse(discard)

    def test_existing_empty_names_do_not_reserve_number_slots(self):
        ws,outputs,tree=fixture({'left':[('5',0),('8',1),('9',0)],'center':[('12',1)],'right':[('2',1)]})
        targets,peeks,discard=numbering_plan(ws,outputs,tree)
        by_name={w['name']:w for w in ws}
        self.assertEqual(targets[by_name['8']['id']],'1')
        self.assertEqual(targets[by_name['5']['id']],'4')
        self.assertEqual([w['name'] for w in discard],['9'])
        self.assertEqual([p['name'] for p in peeks],['4','5','6'])

    def test_empty_center_has_only_its_required_visible_workspace(self):
        ws,outputs,tree=fixture({'left':[('1',1),('8',1)],'center':[('7',0)],'right':[('3',1)]})
        targets,peeks,discard=numbering_plan(ws,outputs,tree)
        center=next(w for w in ws if w['output']=='center')
        self.assertEqual(targets[center['id']],'2')
        self.assertEqual(peeks[1]['id'],center['id'])
        self.assertNotIn('5',targets.values())

    def test_all_empty_outputs_have_only_one_numeric_workspace_each(self):
        ws,outputs,tree=fixture({'left':[('18',0)],'center':[('17',0)],'right':[('16',0)]})
        targets,peeks,discard=numbering_plan(ws,outputs,tree)
        self.assertEqual(set(targets.values()),{'1','2','3'})
        self.assertFalse(discard)

    def test_more_than_nine_uses_existing_win_zero_convention(self):
        ws,outputs,tree=fixture({'left':[(str(i),1) for i in (1,4,7,10)],'center':[('2',1)],'right':[('3',1)]})
        targets,_,_=numbering_plan(ws,outputs,tree)
        self.assertEqual([targets[w['id']] for w in ws if w['output']=='left'],['1','4','7','10'])


class NewWorkspaceTests(unittest.TestCase):
    def test_each_focused_output_uses_its_own_first_free_number(self):
        ws,outputs,_=fixture({'left':[('1',1),('4',1)],'center':[('2',1),('5',0)],'right':[('3',1)]})
        for output,expected in [('left','7'),('center','8'),('right','6')]:
            for w in ws:w['focused']=w['output']==output and w['visible']
            self.assertEqual(next_workspace(ws,outputs),(expected,output))

    def test_gaps_and_numbered_prefixes_are_not_reused_as_new_workspaces(self):
        ws,outputs,_=fixture({'left':[('1',1),('7',1)],'center':[('4: app',1)],'right':[('3',1)]})
        next(w for w in ws if w['name']=='4: app')['num']=4
        self.assertEqual(next_workspace(ws,outputs),('10','left'))
        ws=[w for w in ws if w['name']!='4: app']
        self.assertEqual(next_workspace(ws,outputs),('4','left'))

    def test_single_active_output_and_no_focus(self):
        ws,outputs,_=fixture({'left':[('1',1),('3',1)]})
        self.assertEqual(next_workspace(ws,outputs),('2','left'))
        for w in ws:w['focused']=False
        with self.assertRaises(RuntimeError):next_workspace(ws,outputs)


class FakeI3:
    def __init__(self,ws,outputs,tree):self.ws,self.outputs,self.tree=ws,outputs,tree;self.commands=[]
    def query(self,kind):return {'get_workspaces':self.ws,'get_outputs':self.outputs,'get_tree':self.tree}[kind]
    def session(self):return 'test-session'
    def pointer(self):return (100,100)
    def move_pointer(self,_):pass
    def command(self,commands):self.commands.extend(commands)


class ToggleTests(unittest.TestCase):
    def test_show_uses_collision_safe_rename_and_never_moves_application(self):
        ws,outputs,tree=fixture({'left':[('3',1),('8',1)],'center':[('1',1)],'right':[('2',1)]})
        fake=FakeI3(ws,outputs,tree)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json';state=DesktopToggle(fake,path).show()
            self.assertEqual([w['name'] for w in state['workspaces']],['1','2','3'])
            self.assertEqual(state['temporary'],['7','5','6'])
            self.assertEqual(json.loads(path.read_text())['version'],2)
            self.assertFalse(any('move ' in command or command.endswith('kill') for command in fake.commands))
            final_renames=[c for c in fake.commands if c.startswith('rename') and ' to "_desktop_rename_' not in c]
            self.assertEqual(len(final_renames),4)
            self.assertTrue(all('DESKTOP-' not in c for c in fake.commands))

    def test_windowless_placeholder_is_removed_even_in_reused_desktop(self):
        ws,outputs,tree=fixture({'left':[('1',1),('7',0)],'center':[('2',0)],'right':[('3',1)]})
        empty=next(n for n,_ in walk(tree) if n.get('name')=='7')
        empty['nodes']=[dict(id=666,type='con',window=None,nodes=[],floating_nodes=[])]
        fake=FakeI3(ws,outputs,tree)
        with tempfile.TemporaryDirectory() as directory:
            DesktopToggle(fake,Path(directory)/'state.json').show()
        self.assertEqual([c for c in fake.commands if c.endswith('kill')],['[con_id=666] kill'])

    def test_restore_follows_workspace_id_without_undoing_numeric_names(self):
        ws,outputs,tree=fixture({'left':[('4',1)],'center':[('2',0)],'right':[('3',1)]})
        fake=FakeI3(ws,outputs,tree)
        with tempfile.TemporaryDirectory() as directory:
            toggle=DesktopToggle(fake,Path(directory)/'state.json')
            saved=dict(version=2,session='test-session',workspaces=[dict(ws[0],name='8')],fullscreen=[],sticky=[],focused=ws[0]['id'],focused_workspace=dict(ws[0],name='8'))
            toggle.restore(saved)
            self.assertTrue(any('workspace --no-auto-back-and-forth "4"'==c for c in fake.commands))
            self.assertFalse(any('rename' in c or 'move ' in c for c in fake.commands))

    def test_missing_output_or_closed_window_does_not_move_other_windows(self):
        ws,outputs,tree=fixture({'left':[('1',1)],'center':[('2',0)],'right':[('3',1)]})
        outputs[2]['active']=False
        fake=FakeI3(ws,outputs,tree)
        with tempfile.TemporaryDirectory() as directory:
            DesktopToggle(fake,Path(directory)/'state.json').restore(dict(workspaces=ws,focused=99999,focused_workspace=ws[0],fullscreen=[dict(id=99999,mode=2)],sticky=[99999]))
            self.assertFalse(any('move ' in c or '99999' in c or 'right' in c for c in fake.commands))

if __name__=='__main__':unittest.main()
