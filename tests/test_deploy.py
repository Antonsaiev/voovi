"""Preflight развёртывания проверяется без Docker и production-каталогов."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class DeployPreflightTests(unittest.TestCase):
    def run_deploy(self, enabled, folders_exist):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'deploy.sh', root / 'deploy.sh')
        binary = root / 'bin'
        binary.mkdir()
        sources = [root / 'bank-downloads', root / 'bank-prepared']
        if folders_exist:
            for path in sources:
                path.mkdir()
        config = {'services': {'svoovi-api-tasks': {
            'environment': {'BANK_AUTOMATION_ENABLED': str(int(enabled))},
            'volumes': [{'type': 'bind', 'source': str(path),
                         'target': '/usr/src/app/var/banking/' + target}
                        for path, target in zip(sources, ('incoming', 'prepared'))]}}}
        configuration = root / 'compose.json'
        configuration.write_text(json.dumps(config))
        log = root / 'commands.log'
        stub = binary / 'docker'
        stub.write_text('''#!/usr/bin/env python3
import os, pathlib, sys
with open(os.environ['DEPLOY_TEST_LOG'], 'a') as stream:
    stream.write(' '.join(sys.argv[1:]) + '\\n')
if 'config' in sys.argv:
    print(pathlib.Path(os.environ['DEPLOY_TEST_CONFIG']).read_text())
''')
        stub.chmod(0o700)
        env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'],
                   DEPLOY_TEST_LOG=str(log), DEPLOY_TEST_CONFIG=str(configuration))
        result = subprocess.run(['bash', str(root / 'deploy.sh')], env=env,
                                capture_output=True, text=True)
        return result, root, log.read_text().splitlines()

    def test_disabled_bank_missing_folders_does_not_block_common_worker(self):
        result, root, commands = self.run_deploy(False, False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((root / 'analitic/uploads/payments').is_dir())
        self.assertTrue(any(' build' in command for command in commands))
        self.assertTrue(any(' up ' in command for command in commands))

    def test_enabled_bank_missing_folder_stops_before_replacing_containers(self):
        result, root, commands = self.run_deploy(True, False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Нет папок получения банковских файлов', result.stderr)
        self.assertFalse(any(' build' in command or ' up ' in command for command in commands))

    def test_enabled_bank_existing_folders_pass_preflight(self):
        result, root, commands = self.run_deploy(True, True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(' up ' in command for command in commands))


if __name__ == '__main__':
    unittest.main()
