import os

with_gnatpython = False
if not os.environ.get('WITHOUT_GNATPYTHON'):
    try:
        from gnatpython.ex import Run
        from gnatpython.testsuite import Testsuite as BaseTestsuite
    except ImportError:
        pass
    else:
        with_gnatpython = True
if not with_gnatpython:
    from testsuite_support.polyfill import BaseTestsuite, Run


import testsuite_support.adalog_driver
import testsuite_support.python_driver


class Testsuite(BaseTestsuite):
    TEST_SUBDIR = 'tests'
    DRIVERS = {
        'python': testsuite_support.python_driver.PythonDriver,
        'adalog': testsuite_support.adalog_driver.AdalogDriver
    }

    def add_options(self):
        self.main.add_option(
            '--with-python', default=None,
            help='If provided, use as the Python interpreter in testcases.')

        #
        # Convenience options for developpers
        #

        # Tests update
        self.main.add_option(
            '--rewrite', '-r', action='store_true',
            help='Rewrite test baselines according to current output.'
        )

    def tear_up(self):
        # Build Adalog so that each testcase do not try to build it in
        # parallel.
        p = Run(['gprbuild', '-p', '-q', '-P',
                 os.path.join(self.root_dir, '..', 'langkit', 'adalog',
                              'adalog.gpr')])
        if p.status != 0:
            raise RuntimeError(
                'Adalog build failed (GPRbuild returned {})'.format(p.status)
            )
