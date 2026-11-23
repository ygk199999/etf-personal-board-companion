import copy
import json
from pathlib import Path
import tempfile
import unittest
from configuration import ConfigStore, validate_config, settings_from_config
from finance import rebalance
from kb import parse_account


EXAMPLE = json.loads((Path(__file__).parent / 'config.example.json').read_text('utf-8-sig'))


class ConfigTests(unittest.TestCase):
    def test_new_ticker_flows_into_allocation_and_broker_parser(self):
        cfg = copy.deepcopy(EXAMPLE)
        cfg['assets'] = [{'symbol': 'VTI', 'target_percent': 90, 'quantity': 2}]
        cfg['rebalance'].update(cash_target_percent=10, cash_usd=100, drift_threshold_pp=0)
        cfg = validate_config(cfg)
        settings = settings_from_config(cfg)
        result = rebalance(settings, {'VTI': {'price': 100}}, ('VTI',))
        self.assertTrue(result['ready'])
        self.assertAlmostEqual(result['targetCash'], 30)
        self.assertAlmostEqual(result['rows'][0]['delta'], 70)
        self.assertEqual(result['rows'][0]['symbol'], 'VTI')
        header = {'resultCode': '200', 'processFlag': 'A'}
        balance = {'dataHeader': header, 'dataBody': {'Record2': [{'is_cd': 'VTI', 'crncy_clsf_nm': 'USD', 'frgn_hld_q_p6': '2.500000'}]}}
        cash = {'dataHeader': header, 'dataBody': {'Record1': [{'crncy_cd': 'USD', 'fcrncy_tfnd': '100'}]}}
        self.assertEqual(parse_account(balance, cash, ('VTI',))['quantities'], {'VTI': 2.5})

    def test_threshold_includes_cash_and_contribution_triggers(self):
        settings = {'targets': {'VTI': 100}, 'quantities': {'VTI': 1}, 'cash': 1, 'threshold': 5}
        result = rebalance(settings, {'VTI': {'price': 100}}, ('VTI',))
        self.assertTrue(result['ready'])
        self.assertFalse(result['needsRebalance'])
        self.assertEqual(result['rows'][0]['delta'], 0)
        settings['contribution'] = 20
        result = rebalance(settings, {'VTI': {'price': 100}}, ('VTI',))
        self.assertTrue(result['needsRebalance'])
        self.assertEqual(result['rows'][0]['delta'], 21)

    def test_invalid_edit_keeps_last_good_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.example.json'
            path.write_text(json.dumps(EXAMPLE), 'utf-8')
            store = ConfigStore(path)
            good, revision, _ = store.load()
            path.write_text('{ broken json', 'utf-8')
            current, _, error = store.load()
            self.assertEqual(current, good)
            self.assertIn('config.json', error)
            with self.assertRaises(ValueError):
                store.save_settings(settings_from_config(good), revision)
            self.assertEqual(path.read_text(), '{ broken json')

    def test_revision_conflict_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.example.json'
            path.write_text(json.dumps(EXAMPLE), 'utf-8')
            store = ConfigStore(path)
            good, revision, _ = store.load()
            settings = settings_from_config(good)
            settings['contribution'] = 321
            saved, new_revision, _ = store.save_settings(settings, revision)
            self.assertEqual(saved['rebalance']['contribution_usd'], 321)
            self.assertNotEqual(revision, new_revision)
            with self.assertRaises(ValueError):
                store.save_settings(settings, revision)

    def test_bad_values_and_duplicate_symbols(self):
        for field, value in [('refresh_seconds', 1), ('currency', 'KRW'), ('assets', [])]:
            cfg = copy.deepcopy(EXAMPLE); cfg[field] = value
            with self.assertRaises(ValueError): validate_config(cfg)
        cfg = copy.deepcopy(EXAMPLE); cfg['assets'].append(copy.deepcopy(cfg['assets'][0]))
        with self.assertRaises(ValueError): validate_config(cfg)


if __name__ == '__main__':
    unittest.main()
