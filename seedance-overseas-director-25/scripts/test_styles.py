import copy
import json
import tempfile
import unittest
from pathlib import Path

from validate_styles import load_catalog, resolve_style


CATALOG = Path(__file__).resolve().parents[1] / 'styles' / 'catalog.json'


class StyleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'catalog.json'
        self.data = json.loads(CATALOG.read_text(encoding='utf-8'))
        (self.root / 'zootopia.md').write_text('# Test profile', encoding='utf-8')

    def save(self):
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        return load_catalog(self.path)

    def test_real_catalog_and_aliases(self):
        data, index = load_catalog(CATALOG)
        for choice in ['疯狂动物城', '#疯狂动物城', ' ZOotoPIa ', '风格标签：疯狂动物城']:
            self.assertEqual(resolve_style(data, index, choice)['id'], 'zootopia')
        self.assertEqual(resolve_style(data, index)['id'], data['default_style_id'])

    def test_generic_has_no_profile(self):
        data, index = self.save()
        self.assertNotIn('profile', resolve_style(data, index, '通用3D'))

    def test_unknown_does_not_fall_back(self):
        data, index = self.save()
        with self.assertRaisesRegex(ValueError, 'Unregistered'):
            resolve_style(data, index, 'unknown-work')

    def test_new_profile_without_changes_to_director(self):
        entry = copy.deepcopy(self.data['styles'][0])
        entry.update(id='new-style', label='新风格', aliases=['NEW'], profile='new.md')
        self.data['styles'].append(entry)
        (self.root / 'new.md').write_text('# New profile', encoding='utf-8')
        data, index = self.save()
        self.assertEqual(resolve_style(data, index, 'new')['id'], 'new-style')
        self.assertEqual(data['default_style_id'], 'zootopia')

    def test_collision_with_generic_rejected(self):
        self.data['styles'][0]['aliases'].append('通用3D')
        with self.assertRaisesRegex(ValueError, 'collision'):
            self.save()

    def test_unknown_default_rejected(self):
        self.data['default_style_id'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'default_style_id'):
            self.save()

    def test_missing_profile_rejected(self):
        self.data['styles'][0]['profile'] = 'missing.md'
        with self.assertRaisesRegex(ValueError, 'existing .md'):
            self.save()

    def test_profile_cannot_escape_styles(self):
        self.data['styles'][0]['profile'] = '../outside.md'
        with self.assertRaisesRegex(ValueError, 'inside styles'):
            self.save()

    def test_duplicate_ids_rejected(self):
        self.data['styles'].append(copy.deepcopy(self.data['styles'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.save()


if __name__ == '__main__':
    unittest.main()
