#!/usr/bin/env python3
"""Real native addon effects; --protected uses Codex command/exec, without a model.

Reuse the maintained cache helper's protected command and ordered cleanup owner.
Host execution establishes semantics only; native image proof needs --protected.
"""
import io
import json
from pathlib import Path
import tarfile
import unittest

import test_npm_cache as cache


class NativeNodeTests(cache.NpmCacheTests):
    def setUp(self):
        super().setUp()
        # A dependency tarball forces npm's actual dependency lifecycle, not a
        # project-only script or a prebuilt/JS substitute. Only this fixture is
        # allowed to run scripts by the fixture project's own package contract.
        with tarfile.open(self.tarball, 'w:gz') as archive:
            files = {
                'package.json': json.dumps({'name': 'cold-fixture', 'version': '1.0.0',
                                           'main': 'build/Release/addon.node',
                                           'scripts': {'install': 'node-gyp rebuild --verbose',
                                                       'configure': 'node-gyp configure --verbose'}}),
                'binding.gyp': json.dumps({'targets': [{'target_name': 'addon', 'sources': ['addon.c']}]}),
                'addon.c': '#include <node_api.h>\n'
                           'static napi_value init(napi_env env, napi_value exports) {\n'
                           '  napi_value value; napi_create_int32(env, 741, &value); return value;\n'
                           '}\nNAPI_MODULE(NODE_GYP_MODULE_NAME, init)\n',
            }
            for name, data in files.items():
                payload = data.encode()
                info = tarfile.TarInfo('package/' + name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))

    def package(self, directory):
        super().package(directory)
        path = directory / 'package.json'
        value = json.loads(path.read_text())
        value['allowScripts'] = {'file:' + str(self.tarball): True}
        path.write_text(json.dumps(value))

    def run_command(self, script, cwd=None, env=None, login=False, success=True):
        return super().run_command(script, cwd=cwd,
                                   env={'npm_config_foreground_scripts': 'true',
                                        'npm_config_strict_allow_scripts': 'true', **(env or {})},
                                   login=login, success=success)

    def test_native_cold_rebuild_and_nested_lifecycle(self):
        self.run_command('node -p "JSON.stringify({version:process.version,arch:process.arch,'
                         'prefix:process.config.variables.node_prefix,'
                         'discovery:process.config.variables.use_prefix_to_find_headers})"; npm --version')
        self.assertFalse((self.project / 'node_modules').exists())
        self.assertFalse((self.home / '.cache').exists())
        # Existing inherited cache checks also cover login/noninteractive/nested
        # clone/install/lifecycle; now they consume this genuine native addon.
        self.test_cold_install_lifecycle_login_nested_clone_and_pruned_recovery()
        binary = self.project / 'node_modules/cold-fixture/build/Release/addon.node'
        self.assertTrue(binary.is_file())
        binary.unlink()
        self.run_command('npm rebuild cold-fixture --offline --foreground-scripts; npm run build', login=True)
        self.assertTrue(binary.is_file())
        self.run_command('node /usr/local/lib/node_modules/npm/node_modules/node-gyp/bin/node-gyp.js '
                         'rebuild --verbose; node -e \"if(require(\'./build/Release/addon.node\')!==741)process.exit(1)\"',
                         cwd=binary.parents[2])
        self.assertFalse((self.home / '.cache').exists())
        self.run_command('test ! -e "$HOME/.npm"; test ! -e "$HOME/.cache/node-gyp"')
        makefile = (binary.parents[1] / 'addon.target.mk').read_text()
        self.assertIn('/usr/local/include/node', makefile)

    def test_node_gyp_explicit_nodedir_is_consumed(self):
        # Observe the real compiler error/path: npm config output alone cannot
        # establish which options node-gyp used. Keep every bad path distinct.
        self.run_command('npm install --offline --no-audit --no-fund')
        for key in ('npm_config_nodedir', 'NPM_CONFIG_NODEDIR',
                    'npm_package_config_node_gyp_nodedir'):
            bad = str(self.root / key)
            code, stdout, stderr = self.run_command(
                'npm rebuild cold-fixture --offline --foreground-scripts',
                env={key: bad}, success=False)
            self.assertNotEqual(code, 0)
            self.assertIn(bad, stdout + stderr)
        newer = str(self.root / 'newer-setting')
        code, stdout, stderr = self.run_command(
            'npm rebuild cold-fixture --offline --foreground-scripts',
            env={'npm_config_nodedir': '/usr/local',
                 'npm_package_config_node_gyp_nodedir': newer}, success=False)
        self.assertNotEqual(code, 0)
        self.assertIn(newer, stdout + stderr)
        addon = self.project / 'node_modules/cold-fixture'
        cli = str(self.root / 'cli-header-source')
        code, stdout, stderr = self.run_command(
            'npm run configure -- --nodedir=' + cli, cwd=addon, success=False)
        self.assertNotEqual(code, 0)
        self.assertIn(cli, stdout + stderr)
        for location in (self.home / '.npmrc', self.project / '.npmrc', self.root / 'global.npmrc'):
            chosen = str(self.root / ('headers-' + location.parent.name))
            location.write_text('nodedir=' + chosen + '\n')
            env = {'NPM_CONFIG_GLOBALCONFIG': str(location)} if location.name == 'global.npmrc' else {}
            code, stdout, stderr = self.run_command(
                'npm rebuild cold-fixture --offline --foreground-scripts', env=env, success=False)
            self.assertNotEqual(code, 0)
            self.assertIn(chosen, stdout + stderr)
            location.unlink()
        package_path = addon / 'package.json'
        package = json.loads(package_path.read_text())
        package['config'] = {'node_gyp_nodedir': str(self.root / 'package-headers')}
        package_path.write_text(json.dumps(package))
        code, stdout, stderr = self.run_command('npm run configure', cwd=addon, success=False)
        self.assertNotEqual(code, 0)
        self.assertIn(package['config']['node_gyp_nodedir'], stdout + stderr)
        del package['config']
        package_path.write_text(json.dumps(package))
        code, stdout, stderr = self.run_command(
            'npm rebuild cold-fixture --offline --foreground-scripts',
            env={'npm_package_config_node_gyp_target': '0.0.1'}, success=False)
        self.assertNotEqual(code, 0)
        self.assertIn('0.0.1', stdout + stderr)
        # The image default remains available after removing explicit settings.
        self.run_command('npm rebuild cold-fixture --offline --foreground-scripts; npm run build')

    def test_explicit_header_source_is_not_bypassed(self):
        self.run_command('npm install --offline --no-audit --no-fund')
        for key, value in (
                ('npm_package_config_node_gyp_tarball', str(self.root / 'missing-headers.tar.gz')),
                ('npm_package_config_node_gyp_dist_url', 'https://invalid.example'),
                ('NODEJS_ORG_MIRROR', 'https://invalid.example')):
            with self.subTest(configuration=key):
                code, stdout, stderr = self.run_command(
                    'npm rebuild cold-fixture --offline --foreground-scripts',
                    env={key: value, 'npm_package_config_node_gyp_devdir':
                         str(self.root / 'explicit-header-cache')}, success=False)
                self.assertNotEqual(code, 0,
                                    'explicit header source was bypassed: ' + key + '\n' + stdout + stderr)

    @unittest.skipUnless(cache.PROTECTED, 'requires actual protected command boundary')
    def test_home_config_and_image_headers_remain_denied(self):
        header = Path('/usr/local/include/node/node.h')
        original_header = header.read_bytes()
        self.assertIn(b'NODE_H_', original_header)
        config = self.home / '.npmrc'
        config.write_text('# synthetic configuration canary\n')
        for script in ('printf forbidden > "$HOME/.npmrc"',
                       'mkdir "$HOME/forbidden"',
                       'printf forbidden > /usr/local/include/node/node.h'):
            code, _, _ = self.run_command(script, success=False)
            self.assertNotEqual(code, 0)
        self.assertEqual(config.read_text(), '# synthetic configuration canary\n')
        self.assertEqual(header.read_bytes(), original_header)


if __name__ == '__main__':
    unittest.main(verbosity=2)
