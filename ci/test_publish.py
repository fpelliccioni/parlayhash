"""Check publication boundaries with a fixture CLI; never contact a server."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PUBLISH = Path(__file__).resolve().with_name('publish.sh')


class PublishTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        tools = self.root / 'bin'
        tools.mkdir()
        self.log = self.root / 'calls.jsonl'
        self.env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'],
                        GITHUB_EVENT_NAME='push', GITHUB_REF='refs/heads/release/1.0.1',
                        CONAN_LOGIN_USERNAME='fixture', CONAN_PASSWORD='fixture',
                        CONAN_LOGIN_USERNAME_KTH='fixture', CONAN_PASSWORD_KTH='fixture',
                        CONAN_HOME=str(self.root / 'initial-cache'),
                        FIXTURE_LOG=str(self.log))
        program = '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
credentials = [k for k in os.environ if k.startswith(('CONAN_LOGIN_USERNAME', 'CONAN_PASSWORD'))]
record = {'command': args[0], 'credentials_present': bool(credentials), 'home': os.environ['CONAN_HOME']}
if args[0] == 'create':
    assert args[args.index('--version') + 1] == '1.0.1'
if args[0] in ('install', 'test'):
    assert not credentials, 'Credentials reached anonymous verification'
    assert '-cc' in args and 'core:non_interactive=True' in args, 'Interactive authentication is enabled'
    assert '--build=never' in args
    assert any('parlayhash/1.0.1#fixture-revision' in arg for arg in args)
with open(os.environ['FIXTURE_LOG'], 'a') as log:
    log.write(json.dumps(record) + '\\n')
if args[0] == 'list':
    print(json.dumps({'Local Cache': {'parlayhash/1.0.1': {'revisions': {'fixture-revision': {}}}}}))
if args[0] == 'install' and os.environ.get('FAIL_ANONYMOUS'):
    sys.exit(17)
'''
        executable = tools / 'conan'
        executable.write_text('#!' + sys.executable + '\n' + program)
        executable.chmod(0o755)

    def run_publish(self, **overrides):
        return subprocess.run(['bash', str(PUBLISH)], cwd=self.root,
                              env=dict(self.env, **overrides),
                              capture_output=True, text=True, timeout=30)

    def test_exact_revision_uses_fresh_anonymous_cache(self):
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        anonymous = [c for c in calls if c['command'] in ('install', 'test')]
        self.assertEqual([c['command'] for c in anonymous], ['install', 'test'])
        self.assertTrue(next(c for c in calls if c['command'] == 'upload')['credentials_present'])
        for call in anonymous:
            self.assertFalse(call['credentials_present'])
            self.assertNotEqual(call['home'], self.env['CONAN_HOME'])
            self.assertFalse(Path(call['home']).exists(), 'Temporary cache was not cleaned')

    def test_anonymous_download_failure_fails_publication(self):
        result = self.run_publish(FAIL_ANONYMOUS='1')
        self.assertNotEqual(result.returncode, 0)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(calls[-1]['command'], 'install')
        self.assertNotIn('test', [c['command'] for c in calls])

    def test_other_events_are_rejected_before_using_credentials(self):
        for event, ref in [('push', 'refs/heads/main'),
                           ('pull_request', 'refs/heads/release/1.0.1'),
                           ('workflow_dispatch', 'refs/heads/release/1.0.1'),
                           ('push', 'refs/heads/release/invalid'),
                           ('push', 'refs/tags/v1.0.1')]:
            with self.subTest(event=event, ref=ref):
                result = self.run_publish(GITHUB_EVENT_NAME=event, GITHUB_REF=ref)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Publication requires', result.stderr)
                self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()
