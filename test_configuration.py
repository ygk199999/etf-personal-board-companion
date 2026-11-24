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

    def test_wallpaper_preset_selects_profile_without_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.example.json'
            path.write_text(json.dumps(EXAMPLE), 'utf-8')
            store = ConfigStore(path)
            _, revision, _ = store.load()
            properties = {'portfoliojson': {'value': json.dumps({'assets': [{'symbol': 'VTI', 'target_percent': 100}]})},
                          'kbconnect': {'value': True}, 'kbprofile': {'value': 'retirement'}}
            config, revision, error = store.apply_wallpaper_properties(properties, revision)
            self.assertIsNone(error)
            self.assertEqual(config['account'], {'enabled': True, 'profile': 'retirement'})
            self.assertEqual(config['assets'][0]['symbol'], 'VTI')
            for unsafe in [{'appKey': 'not-a-key'}, {'account': {'appSecret': 'not-a-key'}}]:
                with self.assertRaises(ValueError):
                    store.apply_wallpaper_properties({'portfoliojson': {'value': json.dumps(unsafe)}}, revision)
            with self.assertRaises(ValueError):
                store.apply_wallpaper_properties({'kbprofile': {'value': '../outside'}}, revision)

    def test_preset_partial_assets_keep_manual_quantity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.example.json'
            data = copy.deepcopy(EXAMPLE)
            data['assets'][0]['quantity'] = 12.5
            path.write_text(json.dumps(data), 'utf-8')
            store = ConfigStore(path)
            _, revision, _ = store.load()
            config, _, _ = store.apply_wallpaper_properties({'portfoliojson': {'value': '{"assets":[{"symbol":"QQQ","target_percent":100}]}'}}, revision)
            self.assertEqual(config['assets'][0]['quantity'], 12.5)

    def test_account_only_target_is_saved_without_changing_watchlist(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.example.json'
            path.write_text(json.dumps(EXAMPLE), 'utf-8')
            store = ConfigStore(path)
            config, revision, _ = store.load()
            settings = settings_from_config(config)
            settings['targets']['VTI'] = 100
            store.save_settings(settings, revision)
            saved, _, _ = store.load()
            self.assertEqual(saved['rebalance']['position_targets'], {'VTI': 100})
            self.assertNotIn('VTI', [a['symbol'] for a in saved['assets']])


class ManualAccountTests(unittest.TestCase):
    def sample(self):
        from configuration import validate_manual_accounts
        return validate_manual_accounts([{'id': 'isa', 'cash': 100, 'as_of': '2026-11-23',
            'cash_target_percent': 10, 'contribution': 100, 'drift_threshold_pp': 0,
            'holdings': [{'symbol': '069500', 'name': 'ETF', 'quantity': 9, 'value': 900, 'target_percent': 90}]},
            {'id': 'irp'}])

    def test_accounts_remain_separate_and_unknown_is_not_zero(self):
        from configuration import manual_portfolios
        isa, irp = manual_portfolios(self.sample())
        self.assertEqual(isa['total'], 1000)
        self.assertAlmostEqual(isa['rows'][0]['delta'], 90)
        self.assertAlmostEqual(isa['rows'][0]['shares'], .9)
        self.assertTrue(isa['ready'])
        self.assertIsNone(irp['total'])
        self.assertFalse(irp['ready'])

    def test_unknown_quantity_still_calculates_amount(self):
        from configuration import manual_portfolios
        accounts = self.sample()
        accounts[0]['holdings'][0]['quantity'] = None
        row = manual_portfolios(accounts)[0]['rows'][0]
        self.assertEqual(row['delta'], 90)
        self.assertIsNone(row['shares'])

    def test_input_validation(self):
        from configuration import validate_manual_accounts
        for invalid in [None, [{'id': 'isa'}, {'id': 'isa'}], [{'id': 'irp', 'cash': -1}],
                        [{'id': 'irp', 'cash': float('nan')}], [{'id': 'irp', 'as_of': '2026-02-30'}],
                        [{'id': 'irp', 'holdings': [{'symbol': 'A', 'value': -1}]}]]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_manual_accounts(invalid)

    def test_manual_save_preserves_broker_and_watchlist_and_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.example.json'
            path.write_text(json.dumps(EXAMPLE), 'utf-8')
            store = ConfigStore(path)
            old, revision, _ = store.load()
            saved, new_revision, _ = store.save_manual_accounts(self.sample(), revision)
            self.assertEqual(saved['account'], old['account'])
            self.assertEqual(saved['assets'], old['assets'])
            self.assertEqual(len(saved['manual_accounts']), 2)
            with self.assertRaises(ValueError):
                store.save_manual_accounts([], revision)
            props = {'portfoliojson': {'value': json.dumps({'manual_accounts': [{'id': 'irp', 'cash': 0}]})}}
            saved, _, _ = store.apply_wallpaper_properties(props, new_revision)
            self.assertEqual(saved['manual_accounts'][0]['id'], 'irp')


class CompanionIntegrationTests(unittest.TestCase):
    def test_fx_failure_retains_previous_rate_and_marks_error(self):
        from unittest.mock import patch
        import server
        previous = {'rate': 1400, 'fetchedAt': 1}
        with patch.object(server, 'FX', previous), patch.object(server, 'fetch_json', side_effect=ValueError()):
            server.refresh_fx()
            self.assertEqual(server.FX['rate'], 1400)
            self.assertEqual(server.FX['fetchedAt'], 1)
            self.assertTrue(server.FX['error'])

    def test_fx_checks_currency_and_positive_rate(self):
        from unittest.mock import patch
        import server
        for currency, rate in [('USD', 1400), ('KRW', -1), ('KRW', float('nan'))]:
            sample = {'chart': {'result': [{'meta': {'currency': currency, 'regularMarketPrice': rate}}]}}
            with self.subTest(currency=currency, rate=rate), patch.object(server, 'FX', {'rate': None}), patch.object(server, 'fetch_json', return_value=sample):
                server.refresh_fx()
                self.assertIsNone(server.FX['rate'])
                self.assertTrue(server.FX['error'])

    def test_native_editor_opens_only_live_config_and_debounces(self):
        from unittest.mock import patch
        import server
        with patch.object(server, 'LAST_EDITOR_OPEN', 0), patch.object(server.time, 'monotonic', return_value=100), patch.object(server.subprocess, 'Popen') as launch:
            server.open_config_editor()
            server.open_config_editor()
            launch.assert_called_once()
            args, kwargs = launch.call_args
            self.assertEqual(args[0][1], str(server.CONFIGS.path.resolve()))
            self.assertFalse(kwargs['shell'])


if __name__ == '__main__':
    unittest.main()
