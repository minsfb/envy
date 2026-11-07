import json
from pathlib import Path
import shutil
import subprocess
import tempfile
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


if __name__ == '__main__':
    unittest.main()
