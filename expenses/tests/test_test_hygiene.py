"""Guards for how the tests themselves are written."""
import ast
from pathlib import Path

from django.test import SimpleTestCase

TESTS = Path(__file__).resolve().parent
# Calls that can skip the whole class (they raise unittest.SkipTest)
CAN_SKIP = {'compile_catalogues', 'skipTest'}


def _calls(node):
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            yield func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', '')


def _is_super_set_up_class(node):
    return any(
        isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == 'setUpClass'
        and isinstance(c.func.value, ast.Call) and getattr(c.func.value.func, 'id', '') == 'super'
        for c in ast.walk(node))


class SetUpClassTests(SimpleTestCase):
    def test_nothing_that_can_skip_the_class_runs_after_super_set_up_class(self):
        """A Django TestCase opens a transaction in setUpClass. If a SkipTest is raised after that, the class is never
        torn down: the transaction stays open and every later test in the same process runs inside it. A test that
        needs a transaction-free connection (the migration test) then fails, but only on some runs, depending on which
        tests shared its worker. So anything that can skip goes *before* ``super().setUpClass()``."""
        offences = []
        for path in sorted(TESTS.rglob('test*.py')):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if not (isinstance(node, ast.FunctionDef) and node.name == 'setUpClass'):
                    continue
                opened = False
                for statement in node.body:
                    if opened and CAN_SKIP & set(_calls(statement)):
                        offences.append(f'{path.relative_to(TESTS.parent.parent)}:{statement.lineno}')
                    if _is_super_set_up_class(statement):
                        opened = True
        self.assertEqual(offences, [], 'move the call that can skip above super().setUpClass()')

    def test_the_guard_notices_the_pattern(self):
        bad = ast.parse(
            'class T:\n'
            '    @classmethod\n'
            '    def setUpClass(cls):\n'
            '        super().setUpClass()\n'
            '        compile_catalogues()\n')
        function = bad.body[0].body[0]
        opened, caught = False, False
        for statement in function.body:
            if opened and CAN_SKIP & set(_calls(statement)):
                caught = True
            if _is_super_set_up_class(statement):
                opened = True
        self.assertTrue(caught)
