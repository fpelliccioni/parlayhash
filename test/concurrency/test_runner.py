"""Exercise mode selection and phase failures without compiling the library."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

RUNNER = Path(__file__).with_name('run_sanitizers.py')


class RunnerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        program = '''import os, sys, time
phase = 'build' if '--build' in sys.argv else ('test' if os.path.basename(sys.argv[0]) == 'ctest' else 'configure')
print('phase=' + phase, flush=True)
if os.environ.get('TIMEOUT_PHASE') == phase: time.sleep(20)
if os.environ.get('FAIL_PHASE') == phase: sys.exit(3)
'''
        for tool in ('cmake', 'ctest'):
            executable = self.bin / tool
            executable.write_text('#!' + sys.executable + '\n' + program)
            executable.chmod(0o755)

    def run_case(self, kind, phase=None):
        output = self.root / (kind + '-' + str(phase))
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'])
        if phase:
            env['FAIL_PHASE' if kind == 'fail' else 'TIMEOUT_PHASE'] = phase
        result = subprocess.run(
            [sys.executable, str(RUNNER), '--output', str(output),
             '--modes', 'none', 'undefined', '--configure-timeout', '2',
             '--build-timeout', '2', '--test-timeout', '2'],
            env=env, capture_output=True, text=True, timeout=30)
        return result, output

    def test_selected_modes_do_not_claim_excluded_modes(self):
        result, output = self.run_case('pass')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((output / 'summary.json').read_text()),
                         {'none': 'passed', 'undefined': 'passed'})
        config = json.loads((output / 'run-config.json').read_text())
        self.assertEqual(config['requested_modes'], ['none', 'undefined'])
        self.assertIn('memory', config['not_requested'])

    def test_phase_failures_are_logged_and_next_mode_still_runs(self):
        for phase in ('configure', 'build', 'test'):
            with self.subTest(phase=phase):
                result, output = self.run_case('fail', phase)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(json.loads((output / 'summary.json').read_text()),
                                 {mode: phase + '_failed' for mode in ('none', 'undefined')})
                self.assertIn('phase=' + phase, (output / f'none-{phase}.log').read_text())

    def test_phase_timeouts_preserve_output_and_next_mode_still_runs(self):
        for phase in ('configure', 'build', 'test'):
            with self.subTest(phase=phase):
                result, output = self.run_case('timeout', phase)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(json.loads((output / 'summary.json').read_text()),
                                 {mode: phase + '_timeout' for mode in ('none', 'undefined')})
                log = (output / f'none-{phase}.log').read_text()
                self.assertIn('phase=' + phase, log)
                self.assertIn('Runner timeout', log)


if __name__ == '__main__':
    unittest.main()
