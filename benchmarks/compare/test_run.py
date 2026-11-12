"""Regression checks for runner validation and paired sampling (no map timings)."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

RUNNER = Path(__file__).with_name("run.py")


class RunnerTests(unittest.TestCase):
    def test_nonfinite_duration_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            for value in ("nan", "inf", "-inf", "0", "-1"):
                output = Path(directory) / "output"
                result = subprocess.run(
                    [sys.executable, str(RUNNER), "--build-dir", directory,
                     "--output", str(output), "--seconds=" + value, "--dry-run"],
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("finite and positive", result.stderr)
                self.assertFalse(output.exists())

    def fixture_run(self, program, extra):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        build = Path(directory.name)
        (build / "comparison-build.txt").write_text("fixture")
        for name in ("parlay_hash", "boost_hash"):
            executable = build / name
            executable.write_text('#!' + sys.executable + '\n' + program)
            executable.chmod(0o755)
        output = build / "output"
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--build-dir", str(build),
             "--output", str(output), "--threads", "1", "--sizes", "10",
             "--updates", "50", "--zipfians", "0.1234567890123456",
             "--rounds", "1", "--seconds", "0.01", *extra],
            capture_output=True, text=True, timeout=20)
        return result, output

    def test_mismatched_or_missing_distribution_and_growth_are_rejected(self):
        valid = dict(n="10", p="1", update="50%", z="0.1234567890123456", grow="1")
        for key, replacement in [("z", "0"), ("z", "nan"), ("z", None),
                                 ("grow", "0"), ("grow", None)]:
            with self.subTest(key=key, replacement=replacement):
                fields = dict(valid)
                if replacement is None:
                    del fields[key]
                else:
                    fields[key] = replacement
                line = ',long_long,' + ','.join(k+'='+v for k, v in fields.items()) + ',mops=1'
                result, output = self.fixture_run('print(' + repr(line) + ')', ['--grow'])
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('inspect', result.stderr)
                self.assertFalse((output / 'results.csv').exists())
                self.assertEqual(len(list(output.glob('*.log'))), 1)

    def test_timeout_preserves_partial_log_and_stops_run(self):
        result, output = self.fixture_run(
            "import time\nprint('partial output', flush=True)\ntime.sleep(10)",
            ['--sample-timeout', '2'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('benchmark timed out', result.stderr)
        logs = list(output.glob('*.log'))
        self.assertEqual(len(logs), 1)
        self.assertIn('partial output', logs[0].read_text())
        self.assertIn('Runner timeout', logs[0].read_text())
        self.assertFalse((output / 'results.csv').exists())


    def test_paired_rounds_and_auditable_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            (build / "comparison-build.txt").write_text("test fixture; not performance data")
            program = '''import os, sys
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
assert args['-r'] == '1'
assert os.environ['PARLAY_NUM_THREADS'] == args['-p']
print(',long_long,n=' + args['-n'] + ',p=' + args['-p'] +
      ',update=' + args['-u'] + '%,z=' + args['-z'] +
      ',grow=0,mops=0.5,insert_mops=1.25,mem_pe=0')
'''
            for name in ("parlay_hash", "boost_hash"):
                executable = build / name
                executable.write_text('#!' + sys.executable + '\n' + program)
                executable.chmod(0o755)
            output = build / "output"
            subprocess.run([sys.executable, str(RUNNER), "--build-dir", directory,
                            "--output", str(output), "--threads", "1", "8",
                            "--sizes", "10", "--updates", "50", "--zipfians", "0",
                            "--rounds", "2", "--seconds", "0.01"],
                           check=True, capture_output=True, text=True)
            with (output / "results.csv").open() as source:
                rows = list(csv.DictReader(source))
            self.assertEqual([r['implementation'] for r in rows],
                             ['parlay_hash', 'boost_hash', 'boost_hash', 'parlay_hash',
                              'boost_hash', 'parlay_hash', 'parlay_hash', 'boost_hash'])
            self.assertEqual([int(r['execution_order']) for r in rows], list(range(1, 9)))
            self.assertEqual([int(r['round']) for r in rows], [1, 1, 2, 2] * 2)
            self.assertEqual([int(r['pair_position']) for r in rows], [1, 2] * 4)
            self.assertEqual(len({r['log'] for r in rows}), 8)
            for row in rows:
                self.assertTrue((output / row['log']).is_file())
                self.assertEqual(float(row['mops']), 0.5)
            metadata = json.loads((output / 'metadata.json').read_text())
            self.assertEqual(metadata['sampling_protocol'], 'paired-process-rounds-v1')
            hardware = metadata['hardware']
            for key in ('cpu_model', 'topology', 'logical_cpus', 'effective_affinity'):
                self.assertIn(key, hardware)
            if hardware['effective_affinity'] is None:
                self.assertIn('affinity_note', hardware)


if __name__ == '__main__':
    unittest.main()
