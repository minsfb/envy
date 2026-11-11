import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'assets/agent-assets/skills/implement/scripts/git-worktree.mjs'


@unittest.skipUnless(shutil.which('node'), 'Node is needed for the portable Git helper')
class GitWorktreePlacement(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'repo with spaces'
        self.root.mkdir()
        self.git(self.root, 'init', '-q')
        self.git(self.root, 'config', 'user.name', 'Fixture')
        self.git(self.root, 'config', 'user.email', 'fixture@example.invalid')
        self.git(self.root, 'commit', '--allow-empty', '-qm', 'base')

    def git(self, root, *args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True, stderr=subprocess.PIPE).strip()

    def call(self, action, cwd=None, branch='feature/task', check=True):
        result = subprocess.run(['node', str(HELPER), action, '--cwd', str(cwd or self.root), '--branch', branch],
                                text=True, capture_output=True, check=check)
        return json.loads(result.stdout) if result.returncode == 0 else result

    def test_plan_and_repeatable_preparation_preserve_source_and_do_not_create_task(self):
        ignore = self.root / '.gitignore'
        ignore.write_text('build/\n# keep this comment', encoding='utf-8')
        before = self.git(self.root, 'status', '--porcelain')
        branches = self.git(self.root, 'branch', '--list')
        plan = self.call('plan')
        self.assertEqual(plan['ownerRoot'], str(self.root))
        self.assertEqual(plan['worktree'], str(self.root / '.worktrees/feature-task'))
        self.assertEqual(self.git(self.root, 'status', '--porcelain'), before)
        self.assertFalse((self.root / '.worktrees').exists())
        prepared = self.call('prepare')
        self.assertFalse(prepared['ignoreMissing'])
        self.assertFalse(prepared['areaMissing'])
        self.assertFalse(Path(prepared['worktree']).exists())
        contents = ignore.read_text()
        self.assertEqual(contents, 'build/\n# keep this comment\n/.worktrees/\n')
        self.call('prepare')
        self.assertEqual(ignore.read_text(), contents)
        self.assertEqual(self.git(self.root, 'branch', '--list'), branches)
        self.assertEqual(self.git(self.root, 'diff', '--cached'), '')

    def test_linked_and_detached_worktrees_reuse_the_primary_area(self):
        self.call('prepare')
        linked = self.root / '.worktrees/task-a'
        self.git(self.root, 'worktree', 'add', '-qb', 'task-a', str(linked), 'HEAD')
        (linked / 'src').mkdir()
        self.assertEqual(self.call('prepare', linked / 'src')['ownerRoot'], str(self.root))
        self.assertFalse((linked / '.worktrees').exists())
        external = Path(self.temp.name) / 'external\ncheckout'
        self.git(self.root, 'worktree', 'add', '--detach', '-q', str(external), 'HEAD')
        self.assertEqual(self.call('plan', external)['area'], str(self.root / '.worktrees'))

    def test_gitfile_with_separate_metadata_has_no_directory_parent_assumption(self):
        separate = Path(self.temp.name).resolve() / 'separate'
        separate.mkdir()
        metadata = Path(self.temp.name).resolve() / 'custom-metadata'
        self.git(separate, 'init', '-q', '--separate-git-dir', str(metadata))
        self.assertEqual(self.call('plan', separate)['ownerRoot'], str(separate))
        # Git records the worktree location in core.worktree for this layout.
        self.git(separate, 'config', 'core.worktree', str(separate))
        self.assertTrue((separate / '.git').is_file())
        self.assertEqual(self.call('plan', separate)['ownerRoot'], str(separate))
        self.assertFalse((metadata / '.worktrees').exists())

    def test_submodule_owns_its_area(self):
        self.git(self.root, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(self.root), 'child')
        child = self.root / 'child'
        self.assertTrue((child / '.git').is_file())
        self.assertEqual(self.call('prepare', child)['area'], str(child / '.worktrees'))
        self.assertFalse((self.root / '.worktrees').exists())

    def test_bare_and_missing_primary_require_explicit_placement(self):
        bare = Path(self.temp.name) / 'bare.git'
        subprocess.run(['git', 'clone', '--bare', '-q', str(self.root), str(bare)], check=True)
        failure = self.call('prepare', bare, check=False)
        self.assertIn('explicit worktree destination', failure.stderr)
        linked = Path(self.temp.name) / 'retained'
        self.git(self.root, 'worktree', 'add', '-qb', 'retained', str(linked), 'HEAD')
        moved = Path(self.temp.name) / 'moved-primary'
        self.root.rename(moved)
        subprocess.run(['git', '--git-dir', str(moved / '.git'), 'config', 'core.worktree', str(self.root)], check=True)
        # Point the retained checkout at moved shared metadata without repairing
        # the registry's primary location, emulating an unavailable checkout.
        (linked / '.git').write_text(f'gitdir: {moved}/.git/worktrees/retained\n')
        failure = self.call('prepare', linked, check=False)
        self.assertNotEqual(failure.returncode, 0)
        self.assertFalse((linked / '.worktrees').exists())

    def test_unsafe_area_ignore_links_and_tracked_contents_are_preserved(self):
        outside = Path(self.temp.name) / 'foreign'
        outside.mkdir()
        (self.root / '.worktrees').symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(self.call('prepare', check=False).returncode, 0)
        self.assertFalse((self.root / '.gitignore').exists())
        (self.root / '.worktrees').unlink()
        foreign_ignore = outside / 'ignore'
        foreign_ignore.write_text('keep\n')
        (self.root / '.gitignore').symlink_to(foreign_ignore)
        self.assertNotEqual(self.call('prepare', check=False).returncode, 0)
        self.assertEqual(foreign_ignore.read_text(), 'keep\n')
        (self.root / '.gitignore').unlink()
        (self.root / '.worktrees').mkdir()
        (self.root / '.worktrees/source').write_text('tracked')
        self.git(self.root, 'add', '.worktrees/source')
        self.assertNotEqual(self.call('prepare', check=False).returncode, 0)
        self.assertFalse((self.root / '.gitignore').exists())

    def test_exclude_only_and_negated_rules_get_a_real_root_ignore(self):
        (self.root / '.git/info/exclude').write_text('/.worktrees/\n')
        self.call('prepare')
        self.assertEqual((self.root / '.gitignore').read_text(), '/.worktrees/\n')
        (self.root / '.gitignore').write_text('/.worktrees/\n!/.worktrees/\n')
        self.call('prepare')
        self.assertEqual((self.root / '.gitignore').read_text(), '/.worktrees/\n!/.worktrees/\n/.worktrees/\n')

    # Piccolo #38: canonical exported placement rejects aliases through absent parents.
    def test_explicit_missing_parent_aliases_cannot_enter_metadata_or_nested_areas(self):
        metadata = Path(self.git(self.root, 'rev-parse', '--absolute-git-dir'))
        alias = Path(self.temp.name) / 'metadata-alias'
        alias.symlink_to(metadata, target_is_directory=True)
        self.call('prepare')
        linked = self.root / '.worktrees/linked'
        self.git(self.root, 'worktree', 'add', '-qb', 'linked', str(linked), 'HEAD')
        linked_alias = Path(self.temp.name) / 'linked-alias'
        linked_alias.symlink_to(linked, target_is_directory=True)
        for target, message in ((alias / 'missing/task', 'Git administrative metadata'),
                                (linked_alias / '.worktrees/missing/task', 'Nested task worktree areas')):
            code = 'import {planWorktreeParent} from ' + json.dumps(HELPER.as_uri()) + '; planWorktreeParent(' + json.dumps(str(self.root)) + ',' + json.dumps(str(target)) + ');'
            result = subprocess.run(['node', '--input-type=module', '-e', code], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(message, result.stderr)
            self.assertFalse(target.parent.exists())

    # Piccolo #38: both processes plan an absent area before either may write it.
    def test_concurrent_area_preparation_preserves_one_ignore_rule(self):
        ignore = self.root / '.gitignore'
        ignore.write_text('build/\n# preserve\n')
        shim = Path(self.temp.name) / 'shim'
        shim.mkdir()
        barrier = self.root / '.git/area-test-barrier'
        barrier.mkdir()
        executable = shim / 'git'
        executable.write_text('#!' + sys.executable + '\n' + '''import os,sys,time
from pathlib import Path
if "check-ignore" in sys.argv:
    root=Path(os.environ["AREA_TEST_BARRIER"])
    (root/str(os.getppid())).touch()
    deadline=time.monotonic()+10
    while len(list(root.iterdir()))<2:
        if time.monotonic()>deadline:sys.exit(3)
        time.sleep(0.02)
os.execv(os.environ["AREA_TEST_REAL_GIT"],[os.environ["AREA_TEST_REAL_GIT"],*sys.argv[1:]])
''')
        executable.chmod(0o700)
        env = dict(os.environ, PATH=str(shim) + os.pathsep + os.environ['PATH'],
                   AREA_TEST_BARRIER=str(barrier), AREA_TEST_REAL_GIT=shutil.which('git'))
        command = ['node', str(HELPER), 'prepare', '--cwd', str(self.root), '--branch', 'feature/task']
        children = [subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for child in children:
            stdout, stderr = child.communicate(timeout=20)
            self.assertEqual(child.returncode, 0, stderr)
            self.assertFalse(json.loads(stdout)['areaMissing'])
        self.assertEqual(ignore.read_text(), 'build/\n# preserve\n/.worktrees/\n')
        self.assertFalse((self.root / '.git/nv-worktree-prepare.lock').exists())
        self.assertFalse((self.root / '.worktrees/feature-task').exists())


if __name__ == '__main__':
    unittest.main()
