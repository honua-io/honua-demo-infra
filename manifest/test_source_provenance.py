import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from source_provenance import PROVENANCE_PATH, bind_provenance, validate_entry


class SourceProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.definition = json.loads(PROVENANCE_PATH.read_text())
        self.entry = copy.deepcopy(self.definition['entries']['maui-buildings'])

    def test_existing_artifacts_have_explicit_receipt_gaps(self):
        for identifier, entry in self.definition['entries'].items():
            validate_entry(identifier, entry)
            self.assertEqual(entry['status'], 'legacy-unattested')
            self.assertFalse(entry['lifecycle']['newDemoEligible'])
            self.assertTrue(all(source['sha256'] is None for source in entry['sources']))
            self.assertIsNone(entry['artifact']['sha256'])
            self.assertTrue(entry['gap'])

    def test_verified_status_requires_original_inputs_and_output(self):
        self.entry['status'] = 'verified'
        with self.assertRaisesRegex(ValueError, 'verified input receipts'):
            validate_entry('buildings', self.entry)
        self.entry['sources'] = [{'role': 'input', 'url': 'https://example.org/data', 'sha256': 'a' * 64}]
        with self.assertRaisesRegex(ValueError, 'verified artifact'):
            validate_entry('buildings', self.entry)
        self.entry['artifact']['sha256'] = 'b' * 64
        validate_entry('buildings', self.entry)

    def test_malformed_hash_and_missing_credit_are_rejected(self):
        for mutation in ('hash', 'credit', 'obligations'):
            entry = copy.deepcopy(self.entry)
            if mutation == 'hash':
                entry['sources'][0]['sha256'] = 'not-a-checksum'
            elif mutation == 'credit':
                entry['license']['attribution'] = ''
            else:
                entry['license']['obligations'] = []
            with self.assertRaises(ValueError):
                validate_entry('buildings', entry)

    def test_discovery_reference_hashes_are_rejected(self):
        self.entry['sources'][0]['sha256'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'discovery references'):
            validate_entry('buildings', self.entry)

    def test_legacy_artifact_cannot_be_reused_by_new_demo(self):
        self.entry['lifecycle']['newDemoEligible'] = True
        with self.assertRaisesRegex(ValueError, 'legacy retirement'):
            validate_entry('buildings', self.entry)

    def test_generated_coverage_binding_and_definition_digest(self):
        manifest = json.loads(Path(__file__).with_name('demo-services.v1.json').read_text())
        services, assets = manifest['services'], manifest['assets']
        for target in [*services, *assets.values()]:
            target.pop('provenance', None)
            target.pop('attribution', None)
        digest = bind_provenance(services, assets)
        self.assertEqual(digest, hashlib.sha256(PROVENANCE_PATH.read_bytes()).hexdigest())
        self.assertEqual(manifest['sources']['sourceProvenanceSha256'], digest)
        buildings = next(service for service in services if service['id'] == 'maui-buildings')
        self.assertEqual(buildings['provenance']['license']['id'], 'ODbL-1.0')
        self.assertIn('share-alike', ' '.join(buildings['provenance']['license']['obligations']))
        self.assertEqual(assets['glyphs']['provenance']['license']['id'], 'OFL-1.1')
        self.assertNotIn('provenance', next(service for service in services if service['id'] == 'demo-stac'))
        with self.assertRaisesRegex(ValueError, 'exactly cover'):
            bind_provenance(services[:-2], assets)
        with tempfile.TemporaryDirectory() as directory:
            altered = copy.deepcopy(self.definition)
            altered['entries']['unknown'] = self.entry
            path = Path(directory) / 'source.json'
            path.write_text(json.dumps(altered))
            with self.assertRaisesRegex(ValueError, 'exactly cover'):
                bind_provenance(services, assets, path)


if __name__ == '__main__':
    unittest.main()
