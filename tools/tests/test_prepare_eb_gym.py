import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('prepare_eb_gym', Path(__file__).parents[1] / 'prepare_eb_gym.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ConversionTests(unittest.TestCase):
    def test_constraint_sql_names_are_preserved_and_conversion_is_idempotent(self):
        source = "class Example:\n    _sql_constraints = [('name_uniq', 'unique(name)', 'Duplicate')]\n"
        result, changes = module.convert_constraints(source)
        self.assertIn("_name_uniq = models.Constraint('unique(name)', 'Duplicate')", result)
        self.assertEqual(changes, [{'class': 'Example', 'name': 'name_uniq'}])
        self.assertEqual(module.convert_constraints(result), (result, []))

    def test_collision_is_rejected_without_overwriting_existing_attribute(self):
        with self.assertRaisesRegex(ValueError, 'collision'):
            module.convert_constraints("class Example:\n    _name_uniq = 1\n    _sql_constraints = [('name_uniq', 'unique(name)', 'Duplicate')]\n")

    def test_preparation_preserves_original_and_refuses_existing_destination(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'original' / 'eb_gym_management'
            source.mkdir(parents=True)
            manifest = "{'version': '19.0.1.3.0', 'license': 'OPL-1'}"
            # Use the vendor manifest's double-quoted version convention.
            manifest = manifest.replace("'19.0.1.3.0'", '"19.0.1.3.0"')
            (source / '__manifest__.py').write_text(manifest)
            output = Path(root) / 'private' / 'eb_gym_management'
            report = module.prepare(source, output)
            self.assertEqual((source / '__manifest__.py').read_text(), manifest)
            self.assertEqual(report['state'], 'source_prepared_installation_unverified')
            self.assertIn('20.0.1.3.0', (output / '__manifest__.py').read_text())
            with self.assertRaisesRegex(ValueError, 'new directory'):
                module.prepare(source, output)

    def test_unknown_vendor_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'eb_gym_management'
            source.mkdir()
            (source / '__manifest__.py').write_text("{'version': '21.0.0', 'license': 'OPL-1'}")
            with self.assertRaisesRegex(ValueError, 'Unexpected source'):
                module.prepare(source, Path(root) / 'new')


if __name__ == '__main__':
    unittest.main()
