import math
import unittest
from unittest.mock import patch, MagicMock
from finance import SYMBOLS, indicators, rebalance, validate_settings
from kb import checked_body, parse_account, parse_domestic, protect, device_header, Client, KBResponseError


def response(body):
    return {"dataHeader": {"resultCode": "200", "processFlag": "A"}, "dataBody": body}


class DipPlanTests(unittest.TestCase):
    def setUp(self):
        import time
        from configuration import validate_dip_strategy
        self.config = validate_dip_strategy({'enabled': True, 'starting_cash_krw': 10000000})
        self.now = int(time.time())

    def quote(self, prices):
        return {'fetchedAt': self.now, 'completedCloses': [(self.now-(len(prices)-i-1)*86400, p) for i,p in enumerate(prices)]}

    def test_ladder_does_not_spend_without_actual_fills(self):
        from finance import dip_plan
        runtime, plan = dip_plan(self.config, self.quote([100, 70]), {})
        self.assertEqual([r['amountKrw'] for r in plan['rows']], [3000000, 2100000, 1470000, 1029000])
        self.assertTrue(all(r['status']=='due' for r in plan['rows']))
        self.assertEqual(plan['cashKrw'], 10000000)
        again, result = dip_plan(self.config, self.quote([100,70]), runtime)
        self.assertEqual(again, runtime)
        self.assertEqual(result['cashKrw'], 10000000)

    def test_fill_and_recovery_preserve_balance_and_reset_steps(self):
        from finance import dip_plan
        runtime, _ = dip_plan(self.config, self.quote([100,85]), {})
        self.config['executions'] = [{'cycle': 1, 'level_percent': 10, 'amount_krw': 3000000, 'date': '2026-11-24'}]
        runtime, plan = dip_plan(self.config, self.quote([100,85]), runtime)
        self.assertEqual(plan['cashKrw'], 7000000)
        self.assertEqual(plan['rows'][0]['status'], 'completed')
        self.assertEqual(plan['rows'][1]['amountKrw'], 2100000)
        # Add one new completed session after the stored timestamp.
        quote = self.quote([100,85,100])
        quote['completedCloses'] = [(t+86400,p) for t,p in quote['completedCloses']]
        runtime, plan = dip_plan(self.config, quote, runtime)
        self.assertEqual(plan['cycle'], 2)
        self.assertEqual(plan['cashKrw'], 7000000)
        self.assertTrue(all(r['status']=='waiting' for r in plan['rows']))

    def test_reference_does_not_roll_down_during_decline(self):
        from finance import dip_plan
        runtime, _ = dip_plan(self.config, self.quote([100,89]), {})
        quote = self.quote([89,85]);quote['completedCloses'] = [(t+86400,p) for t,p in quote['completedCloses']]
        runtime, plan = dip_plan(self.config, quote, runtime)
        self.assertEqual(plan['referenceClose'], 100)
        self.assertAlmostEqual(plan['drawdown'], -15)

    def test_stale_data_and_changed_signal(self):
        from finance import dip_plan
        quote=self.quote([100,85]);quote['fetchedAt']-=600
        _, plan=dip_plan(self.config,quote,{})
        self.assertFalse(plan['ready'])
        runtime,_=dip_plan(self.config,self.quote([100,85]),{})
        self.config['signal_symbol']='QQQ'
        _,plan=dip_plan(self.config,self.quote([100,85]),runtime)
        self.assertEqual(plan['cycle'],2)

    def test_invalid_fills_rejected(self):
        from configuration import validate_dip_strategy
        fill={'cycle':1,'level_percent':10,'amount_krw':3000000,'date':'2026-11-24'}
        with self.assertRaises(ValueError):
            validate_dip_strategy({**self.config,'executions':[fill,fill]})
        with self.assertRaises(ValueError):
            validate_dip_strategy({**self.config,'executions':[{**fill,'amount_krw':10000001}]})

    def test_equal_five_parts_and_actual_fills(self):
        from finance import dip_plan
        self.config.update(allocation_mode='equal_parts', levels_percent=[15,20,25,30,35])
        runtime, plan = dip_plan(self.config, self.quote([100,65]), {})
        self.assertEqual([r['amountKrw'] for r in plan['rows']], [2000000]*5)
        self.assertTrue(all(r['status']=='due' for r in plan['rows']))
        self.assertEqual(plan['cashKrw'],10000000)
        self.config['executions']=[{'cycle':1,'level_percent':15,'amount_krw':2000000,'date':'2026-11-24'}]
        runtime,plan=dip_plan(self.config,self.quote([100,65]),runtime)
        self.assertEqual(plan['cashKrw'],8000000)
        self.assertEqual(plan['rows'][0]['status'],'completed')
        self.assertEqual([r['amountKrw'] for r in plan['rows'][1:]], [2000000]*4)
        # Restart with the persisted state must keep each tranche constant.
        import json
        _,again=dip_plan(self.config,self.quote([100,65]),json.loads(json.dumps(runtime)))
        self.assertEqual(again,plan)

    def test_equal_parts_recovery_and_insufficient_cash(self):
        from finance import dip_plan
        self.config.update(allocation_mode='equal_parts', levels_percent=[15,20,25,30,35])
        runtime,_=dip_plan(self.config,self.quote([100,80]),{})
        self.config['executions']=[{'cycle':1,'level_percent':15,'amount_krw':2000000,'date':'2026-11-24'}]
        quote=self.quote([100,80,100]);quote['completedCloses']=[(t+86400,p) for t,p in quote['completedCloses']]
        runtime,plan=dip_plan(self.config,quote,runtime)
        self.assertEqual(plan['cycle'],2)
        self.assertEqual(plan['cashKrw'],8000000)
        self.assertEqual([r['amountKrw'] for r in plan['rows']], [1600000]*5)
        self.config['cash_adjustment_krw']=-7000000
        _,plan=dip_plan(self.config,quote,runtime)
        self.assertLessEqual(sum(r['amountKrw'] for r in plan['rows']),1000000)


class ContributionPlanTests(unittest.TestCase):
    def setUp(self):
        import time
        from configuration import validate_contribution_plan
        self.config=validate_contribution_plan({'enabled':True,'currency':'USD','amount':200})
        self.quotes={s:{'price':100,'fetchedAt':time.time()} for s in ['QQQ','QQQM','QLD']}
        self.fx={'rate':1000,'fetchedAt':time.time()}

    def plan(self,positions,blocked=None):
        from finance import contribution_plan
        return contribution_plan(self.config,positions,self.quotes,self.fx,blocked)

    def test_shortfall_uses_only_new_money_without_sales(self):
        p=self.plan({'QQQ':10})
        self.assertEqual([r['buyUsd'] for r in p['rows']],[0,200])
        self.assertFalse(p['exact'])
        self.assertAlmostEqual(p['rows'][1]['afterWeight'],100/6)

    def test_qqqm_is_counted_in_core_bucket_and_reaches_target(self):
        self.config['amount']=1000
        p=self.plan({'QQQ':4,'QQQM':6})
        self.assertEqual([r['buyUsd'] for r in p['rows']],[0,1000])
        self.assertTrue(p['exact'])

    def test_balanced_and_reverse_imbalance(self):
        p=self.plan({'QQQ':10,'QLD':10})
        self.assertEqual([r['buyUsd'] for r in p['rows']],[100,100])
        p=self.plan({'QLD':10})
        self.assertEqual([r['buyUsd'] for r in p['rows']],[200,0])

    def test_krw_budget_fx_and_stale_account(self):
        self.config.update(currency='KRW',amount=200000)
        p=self.plan({'QQQ':10})
        self.assertEqual(p['rows'][1]['buyUsd'],200)
        self.assertEqual(p['rows'][1]['buyKrw'],200000)
        self.fx['error']='offline'
        self.assertFalse(self.plan({'QQQ':10})['ready'])
        self.assertFalse(self.plan({'QQQ':10},'stale account')['ready'])

    def test_budget_zero_and_other_holdings_not_counted(self):
        self.config['amount']=0
        p=self.plan({'QQQ':10,'SPY':999})
        self.assertEqual(sum(r['buyUsd'] for r in p['rows']),0)
        self.assertEqual(p['excludedSymbols'],['SPY'])

    def test_automatic_usd_cash_ignores_manual_budget(self):
        from finance import contribution_plan
        self.config.update(funding_source='usd_cash',amount=999999,currency='KRW')
        self.assertFalse(contribution_plan(self.config,{},self.quotes,self.fx)['ready'])
        p=contribution_plan(self.config,{'QQQM':10},self.quotes,self.fx,usd_cash=200)
        self.assertEqual(p['amountUsd'],200)
        self.assertEqual([r['buyUsd'] for r in p['rows']],[0,200])
        self.assertEqual(p['currency'],'USD')

    def test_drawdown_signals_do_not_require_a_cash_reserve(self):
        import time
        from configuration import validate_dip_strategy
        from finance import dip_plan
        cfg=validate_dip_strategy({'enabled':True,'signal_only':True,'starting_cash_krw':0,'levels_percent':[15,20,25,30,35]})
        now=time.time()
        _,p=dip_plan(cfg,{'fetchedAt':now,'completedCloses':[(now-86400,100),(now,80)]},{})
        self.assertEqual([r['status'] for r in p['rows']],['due','due','waiting','waiting','waiting'])
        self.assertTrue(all(r['amountKrw'] is None for r in p['rows']))

    def test_invalid_plan_configuration(self):
        from configuration import validate_contribution_plan
        for values in [{'amount':-1},{'core_symbols':['QQQ','QLD']},{'currency':'EUR'}]:
            with self.assertRaises(ValueError):
                validate_contribution_plan(values)


class FinanceTests(unittest.TestCase):
    def test_drawdown_is_path_dependent(self):
        result = indicators([100, 120, 60, 110])
        self.assertEqual(result["mdd"], -50)
        self.assertAlmostEqual(result["drawdown"], -100 / 12)

    def test_rsi_wilder_reference_and_edges(self):
        prices = [44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28,46.00]
        self.assertAlmostEqual(indicators(prices[:15])["rsi"], 70.464135, places=5)
        self.assertAlmostEqual(indicators(prices)["rsi"], 66.249619, places=5)
        self.assertEqual(indicators(list(range(1, 21)))["rsi"], 100)
        self.assertEqual(indicators(list(range(21, 1, -1)))["rsi"], 0)
        self.assertEqual(indicators([5]*20)["rsi"], 50)
        self.assertIsNone(indicators([5]*10)["rsi"])

    def test_allocation_conserves_money_with_contribution(self):
        settings = {"quantities": {s: 1 for s in SYMBOLS}, "targets": {s: 25 for s in SYMBOLS}, "cash": 100, "contribution": 200}
        quotes = {s: {"price": p} for s, p in zip(SYMBOLS, [100,200,50,50])}
        result = rebalance(settings, quotes)
        self.assertTrue(result["ready"])
        self.assertEqual(result["total"], 500)
        self.assertAlmostEqual(sum(r["delta"] for r in result["rows"]), 300)
        self.assertEqual(result["rows"][1]["delta"], -25)

    def test_unset_or_invalid_targets_disable_calculation(self):
        quotes = {s: {"price": 100} for s in SYMBOLS}
        self.assertFalse(rebalance({}, quotes)["ready"])
        result = rebalance({"targets": {s: 30 for s in SYMBOLS}, "cash": 100}, quotes)
        self.assertTrue(all(r["delta"] is None for r in result["rows"]))
        self.assertFalse(rebalance({}, {})["ready"])
        for invalid in [-1, math.inf, math.nan, True]:
            with self.assertRaises(ValueError):
                validate_settings({"cash": invalid})

    def test_kb_decimal_holdings_and_cash(self):
        rows = [{"is_cd": "QQQ ", "crncy_clsf_nm": "USD ", "frgn_hld_q_p6": "000001.125000"},
                {"is_cd": "QQQ", "crncy_clsf_nm": "USD", "frgn_hld_q_p6": "000000.125000"},
                {"is_cd": "NVDA", "crncy_clsf_nm": "USD", "frgn_hld_q_p6": "1"}]
        b = response({"Record2": rows, "nxt_key": " "})
        c = response({"Record1": [{"crncy_cd": "USD", "fcrncy_tfnd": "0000123.45"}]})
        result = parse_account(b, c)
        self.assertEqual(result["quantities"]["QQQ"], 1.25)
        self.assertEqual(result["cash"], 123.45)
        self.assertEqual(result["excluded"], 0)
        self.assertEqual(result['quantities']['NVDA'], 1)
        self.assertEqual({p['symbol'] for p in result['positions']}, {'QQQ', 'NVDA'})
        b["dataBody"]["nxt_key"] = "more"
        with self.assertRaises(ValueError):
            parse_account(b, c)

    def test_dpapi_round_trip(self):
        original = b"test-only-fake-credential"
        encrypted = protect(original)
        self.assertNotEqual(encrypted, original)
        self.assertEqual(protect(encrypted, decrypt=True), original)

    def test_http_200_is_not_business_success(self):
        with self.assertRaises(ValueError):
            checked_body({"dataHeader": {"resultCode": "200", "processFlag": "E"}, "dataBody": {}})

    def test_kb_device_header_is_not_blank(self):
        route = MagicMock()
        route.__enter__.return_value = route
        route.getsockname.return_value = ('192.0.2.10', 50000)
        with patch('kb.socket.socket', return_value=route), patch('kb.uuid.getnode', return_value=0x102030405060):
            self.assertEqual(device_header(), {'ipAddr': '192.0.2.10', 'macAddr': '10-20-30-40-50-60'})
            route.connect.assert_called_once_with(('developer.kbsec.com', 32484))

    def test_kb_error_never_echoes_response_messages(self):
        header = {'resultCode': '200', 'processFlag': 'B', 'processCode': '9999',
                  'processMessage': 'secret-value [dataHeader.ipAddr]'}
        with self.assertRaises(KBResponseError) as context:
            checked_body({'dataHeader': header})
        self.assertIn('ipAddr', str(context.exception))
        self.assertNotIn('secret-value', str(context.exception))

    def test_unwatched_usd_holdings_and_krw_cash_are_retained(self):
        balance = response({'Record2': [{'is_cd': 'VTI', 'is_nm': 'Total Market', 'crncy_clsf_nm': 'USD',
                                        'frgn_hld_q_p6': '2.500000', 'now_prc_p4': '200.0000'}]})
        cash = response({'Record1': [{'crncy_cd': 'USD', 'fcrncy_tfnd': '0.00'},
                                    {'crncy_cd': 'KRW', 'fcrncy_tfnd': '5000.00'}]})
        account = parse_account(balance, cash, ('QQQ',))
        self.assertEqual(account['quantities']['VTI'], 2.5)
        self.assertEqual(account['positions'][0]['price'], 200)
        self.assertEqual(account['cash_by_currency']['KRW'], 5000)
        self.assertEqual(account['cash'], 0)
        self.assertEqual(account['excluded'], 0)

    def test_domestic_cash_is_informational_and_not_added_to_usd(self):
        summary = parse_domestic(response({'tdy_tfnd_amt': '5000', 'do_psbl_csh': '4000', 'nxt2_dy_tfnd': '4500'}),
                                 response({'nt_asts_val_amt': '15000'}))
        self.assertEqual(summary['cashToday'], 5000)
        self.assertEqual(summary['withdrawableCash'], 4000)
        self.assertEqual(summary['accountNetAssets'], 15000)
        self.assertEqual(summary['currency'], 'KRW')

    def test_missing_domestic_field_is_not_fabricated_zero(self):
        summary = parse_domestic(response({'tdy_tfnd_amt': '0'}), response({}))
        self.assertEqual(summary['cashToday'], 0)
        self.assertIsNone(summary['withdrawableCash'])
        self.assertIsNone(summary['accountNetAssets'])


if __name__ == "__main__":
    unittest.main()
