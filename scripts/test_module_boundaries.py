"""Synthetic negative tests for the static module gate; no product/model/DB imports."""
from __future__ import annotations
import json
from pathlib import Path
import unittest
from check_module_boundaries import (BASE_COMMIT, baseline_document, check, collect_graph, digest,
    forbidden_edge, matrix, codeowners_rules, codeowners_for_path, python_edges, strongly_connected)

ROOT = Path(__file__).resolve().parents[1]
NODE = "/opt/node-v22.18.0-linux-x64/bin/node" if Path("/opt/node-v22.18.0-linux-x64/bin/node").exists() else "node"


def raw(files):
    return {name: value.encode() for name, value in files.items()}


def graph(edges, **extra):
    return {"edges": [{"source": a, "target": b, "line": 1, "specifier": b, "kind": "import"} for a,b in edges], "facades": [], "parse_errors": [], "unresolved": [], "dynamic_unknown": [], **extra}


def empty_baseline():
    return {"legacy_exceptions": [], "cycles": []}


class PythonBoundaryTests(unittest.TestCase):
    def test_absolute_relative_and_submodule_imports_resolve(self):
        files={"engineering/src/ai_application/__init__.py":"", "engineering/src/ai_application/intent.py":"from . import ports\nfrom src.shared_contracts import tool_api\nimport src.shared_contracts\n", "engineering/src/ai_application/ports.py":"", "engineering/src/shared_contracts/__init__.py":"", "engineering/src/shared_contracts/tool_api.py":""}
        pairs={(e['source'],e['target']) for e in python_edges(files)['edges']}
        source='engineering/src/ai_application/intent.py'
        for target in ['engineering/src/ai_application/ports.py','engineering/src/shared_contracts/tool_api.py','engineering/src/shared_contracts/__init__.py']:
            with self.subTest(target=target):self.assertIn((source,target),pairs)

    def test_literal_dynamic_python_import_is_checked(self):
        source='engineering/src/ai_application/a.py'; target='engineering/src/decision/plans.py'
        files={source:"import importlib\nimportlib.import_module('src.decision.plans')\n",target:''}
        parsed=python_edges(files)
        self.assertEqual([e['target'] for e in parsed['edges']],[target])
        self.assertTrue(check(raw(files),{**graph([]),**parsed},empty_baseline(),{}))

    def test_importlib_loader_alias_cannot_bypass_boundary(self):
        a='engineering/src/ai_application/a.py'; b='engineering/src/decision/plans.py'
        files={a:"from importlib import import_module as load\nload('src.decision.plans')",b:''}
        parsed=python_edges(files)
        self.assertIn('forbidden_import',{v['code'] for v in check(raw(files),{**graph([]),**parsed},empty_baseline(),{})})

    def test_new_ai_cannot_import_algorithm_platform_or_agent(self):
        source='engineering/src/ai_application/a.py'
        for target in ['engineering/src/decision/plans.py','engineering/src/platform/store.py','engineering/src/agents/graph.py','engineering/src/cuv_application/api_catalog.py']:
            with self.subTest(target=target):
                violations=check(raw({source:'x',target:'x'}),graph([(source,target)]),empty_baseline(),{})
                self.assertIn('forbidden_import',[v['code'] for v in violations])

    def test_cuv_cannot_import_prompt_or_agent(self):
        for source in ['engineering/src/cuv_application/a.py','engineering/src/decision/plans.py']:
            for target in ['engineering/src/ai_application/a.py','engineering/src/agents/graph.py']:
                with self.subTest(source=source,target=target):self.assertIsNotNone(forbidden_edge(source,target))

    def test_shared_contract_is_not_a_tunnel_to_implementations(self):
        source='engineering/src/shared_contracts/a.py'
        for target in ['engineering/src/platform/authz.py','engineering/src/cuv_application/api_catalog.py','engineering/src/ai_application/intent/a.py','engineering/src/pipe_health/pipeline.py']:
            with self.subTest(target=target):self.assertIsNotNone(forbidden_edge(source,target))

    def test_shared_contract_and_own_module_dependencies_are_allowed(self):
        source='engineering/src/ai_application/a.py'; contract='engineering/src/shared_contracts/tool_api.py'; own='engineering/src/ai_application/rag/ports.py'
        self.assertEqual(check(raw({source:'x',contract:'x',own:'x'}),graph([(source,contract),(source,own)]),empty_baseline(),{}),[])

    def test_frozen_file_cannot_be_changed_or_removed(self):
        name='engineering/src/agents/graph.py'; frozen={'groups':{'agent':{name:digest(b'approved')}}}
        for contents in [{name:b'changed'},{}]:
            with self.subTest(contents=contents):self.assertEqual(check(contents,graph([]),empty_baseline(),frozen)[0]['code'],'frozen_sha')

    def test_historical_edge_requires_exact_pair_and_both_hashes(self):
        a='engineering/src/agents/tools/domain.py'; b='engineering/src/decision/plans.py'
        files=raw({a:'legacy',b:'algorithm'}); g=graph([(a,b)]); baseline=baseline_document(files,g,BASE_COMMIT)
        self.assertEqual(check(files,g,baseline,{}),[])
        changed={**files,b:b'new'}
        self.assertIn('forbidden_import',[v['code'] for v in check(changed,g,baseline,{})])
        c='engineering/src/platform/store.py'
        self.assertIn('forbidden_import',[v['code'] for v in check({**files,c:b'x'},graph([(a,c)]),baseline,{})])

    def test_new_cycles_rejected_legacy_cycles_exactly_pinned(self):
        a='engineering/src/a.py'; b='engineering/src/b.py'; files=raw({a:'a',b:'b'}); g=graph([(a,b),(b,a)])
        self.assertEqual(strongly_connected(g['edges']),[[a,b]])
        self.assertEqual(check(files,g,empty_baseline(),{})[0]['code'],'new_or_changed_cycle')
        baseline=baseline_document(files,g,BASE_COMMIT)
        self.assertEqual(check(files,g,baseline,{}),[])
        self.assertEqual(check({**files,a:b'changed'},g,baseline,{})[0]['code'],'new_or_changed_cycle')

    def test_nonliteral_dynamic_loading_in_new_modules_fails_closed(self):
        name='engineering/src/ai_application/a.py'; files={name:'__import__(user_input)'}; parsed=python_edges(files)
        self.assertEqual(check(raw(files),{**graph([]),**parsed},empty_baseline(),{})[0]['code'],'unresolved_dynamic_import')

    def test_runtime_cannot_route_through_test_module(self):
        self.assertIsNotNone(forbidden_edge('engineering/src/ai_application/a.py','engineering/tests/test_algorithm_bridge.py'))
        self.assertIsNotNone(forbidden_edge('web/src/features/assistant/a.ts','web/src/tests/helper.ts'))

    def test_tests_may_assert_both_sides_without_runtime_exception(self):
        self.assertIsNone(forbidden_edge('engineering/tests/test_module_tool_contracts.py','engineering/src/agents/graph.py'))


class TypeScriptBoundaryTests(unittest.TestCase):
    def parse(self,files):
        files={'web/tsconfig.json':json.dumps({'compilerOptions':{'moduleResolution':'Bundler','module':'ESNext','baseUrl':'.','paths':{'@/*':['src/*']}}}),**files}
        return raw(files),collect_graph(ROOT,raw(files),NODE)

    def test_import_export_dynamic_require_and_aliases_are_resolved(self):
        a='web/src/features/assistant/a.ts'; b='web/src/features/network/b.ts'
        files,g=self.parse({a:"import { b } from '@/features/network/b';\nexport * from '../network/b';\nconst x = import('@/features/network/b');\nconst y = require('../network/b');",b:'export const b = 1;'} )
        self.assertEqual({e['target'] for e in g['edges']},{b})
        self.assertEqual({e['kind'] for e in g['edges']},{'import','export','dynamic_import','require'})
        self.assertEqual(len([v for v in check(files,g,empty_baseline(),{}) if v['code']=='forbidden_import']),4)

    def test_old_compatibility_wrapper_cannot_hide_foreign_feature(self):
        a='web/src/features/cuv/a.ts'; wrapper='web/src/components/AssistantChat.ts'; b='web/src/features/assistant/AssistantChat.ts'
        files,g=self.parse({a:"import { x } from '../../components/AssistantChat';",wrapper:"export * from '../features/assistant/AssistantChat';",b:'export const x=1;'})
        codes={v['code'] for v in check(files,g,empty_baseline(),{})}
        self.assertIn('forbidden_import',codes);self.assertIn('facade_bypass',codes)

    def test_shared_barrel_cannot_hide_foreign_feature(self):
        a='web/src/features/cuv/a.ts'; wrapper='web/src/shared/bridge.ts'; b='web/src/features/assistant/a.ts'
        files,g=self.parse({a:"import { x } from '../../shared/bridge';",wrapper:"export * from '../features/assistant/a';",b:'export const x=1;'})
        codes={v['code'] for v in check(files,g,empty_baseline(),{})}
        self.assertIn('forbidden_import',codes);self.assertIn('facade_bypass',codes)

    def test_nested_reexport_chain_is_followed(self):
        a='web/src/features/cuv/a.ts'; b='web/src/features/assistant/a.ts'; w1='web/src/shared/x.ts'; w2='web/src/shared/y.ts'
        files,g=self.parse({a:"import { x } from '../../shared/x';",w1:"export * from './y';",w2:"export * from '../features/assistant/a';",b:'export const x=1;'})
        self.assertTrue(any(v['code']=='facade_bypass' and v['effective_target']==b for v in check(files,g,empty_baseline(),{})))

    def test_features_may_use_shared_ui_and_own_feature(self):
        a='web/src/features/assistant/a.ts'; b='web/src/shared/ui/Label.ts'; c='web/src/features/assistant/state.ts'
        files,g=self.parse({a:"import { label } from '../../shared/ui/Label'; import { state } from './state';",b:"export const label='a';",c:'export const state=null;'})
        self.assertEqual(check(files,g,empty_baseline(),{}),[])

    def test_shared_surface_cannot_import_legacy_business_engine(self):
        a='web/src/shared/math.ts'; b='web/src/engine/network.ts'
        files,g=self.parse({a:"export * from '../engine/network';",b:'export const calculate=()=>1;'})
        self.assertIn('forbidden_import',{v['code'] for v in check(files,g,empty_baseline(),{})})

    def test_legacy_display_bridge_requires_unchanged_target_sha(self):
        a='web/src/shared/utils/format.ts'; b='web/src/engine/format.ts'
        files,g=self.parse({a:"export * from '../../engine/format';",b:'export const label=()=>"display";'})
        baseline=baseline_document(files,g,BASE_COMMIT)
        self.assertEqual(check(files,g,baseline,{}),[])
        changed={**files,b:b'export const label=()=>"changed";'}
        self.assertIn('shared_bridge_target_changed',{v['code'] for v in check(changed,g,baseline,{})})

    def test_shared_api_cannot_reexport_provider_implementation(self):
        a='web/src/shared/api/index.ts'; b='web/src/providers/platformProvider.ts'
        files,g=self.parse({a:"export * from '../../providers/platformProvider';",b:'export const provider={};'})
        self.assertIn('unreviewed_shared_bridge',{v['code'] for v in check(files,g,empty_baseline(),{})})

    def test_allowed_bridge_must_remain_export_only(self):
        a='web/src/shared/utils/format.ts'; b='web/src/engine/format.ts'
        files,g=self.parse({a:"export * from '../../engine/format'; const extraBehavior=1;",b:'export const label=1;'})
        self.assertIn('unreviewed_shared_bridge',{v['code'] for v in check(files,g,empty_baseline(),{})})

    def test_unresolved_alias_does_not_silently_escape_gate(self):
        a='web/src/features/gas/a.ts'
        files,g=self.parse({a:"import { x } from '@/features/assistant/missing';"})
        self.assertIn('unresolved_local_import',{v['code'] for v in check(files,g,empty_baseline(),{})})

    def test_nonliteral_dynamic_import_is_rejected(self):
        a='web/src/features/assistant/a.ts'; files,g=self.parse({a:'const x = import(userInput);'})
        self.assertIn('unresolved_dynamic_import',{v['code'] for v in check(files,g,empty_baseline(),{})})

    def test_type_import_and_import_equals_are_checked(self):
        a='web/src/features/gas/a.ts';b='web/src/features/assistant/a.ts'
        files,g=self.parse({a:"type A = import('../assistant/a').A; import B = require('../assistant/a');",b:'export type A = string;'})
        self.assertEqual({e['kind'] for e in g['edges']},{'import_type','import_equals'})
        self.assertTrue(check(files,g,empty_baseline(),{}))

    def test_syntax_error_does_not_silently_remove_dependencies(self):
        a='web/src/features/assistant/a.ts';files,g=self.parse({a:'import {'})
        self.assertIn('parse_error',{v['code'] for v in check(files,g,empty_baseline(),{})})


class MatrixTests(unittest.TestCase):
    def test_inventory_has_fields_actual_dependencies_tests_and_migration(self):
        component='web/src/components/AssistantChat.tsx'; moved='web/src/features/assistant/components/AssistantChat.tsx'; test='web/src/tests/assistant.test.ts'
        files=raw({component:'x',test:'x'}); rows=matrix(files,graph([(test,component)]),{},codeowners_rules((ROOT/'.github/CODEOWNERS').read_text()))
        row=next(x for x in rows if x['path']==component)
        self.assertEqual(row['target_path'],moved);self.assertEqual(row['owner'],'SHARED_CONTROLLED');self.assertEqual(row['target_owner'],'A_ONLY');self.assertTrue(row['movable_now']);self.assertEqual(row['required_tests'],[test])
        self.assertEqual(row['responsible_github_accounts'],['catiseyeqaq','chengege985']);self.assertEqual(row['target_responsible_github_accounts'],['catiseyeqaq'])
        self.assertTrue({'path','current_responsibility','direct_dependencies','target_module','owner','shared','frozen','security_contract','movable_now','compatibility','required_tests'}<=row.keys())

    def test_owners_use_verified_accounts_not_tools(self):
        rows=matrix(raw({'web/src/features/gas/a.ts':'x','engineering/src/shared_contracts/a.py':'x'}),graph([]),{},codeowners_rules((ROOT/'.github/CODEOWNERS').read_text()))
        self.assertEqual(rows[0]['owner'],'SHARED_CONTROLLED');self.assertEqual(rows[1]['responsible_github_accounts'],['chengege985'])

class OwnershipConsistencyTests(unittest.TestCase):
    def test_legacy_implementations_and_compatibility_exports_remain_shared(self):
        rules=codeowners_rules((ROOT/'.github/CODEOWNERS').read_text())
        for name in ['engineering/src/adapters/scoring.py','server/demo_engine.py','server/llm_assistant.py','web/src/engine/network.ts','web/src/engine/assistant.ts','web/src/components/AssistantChat.tsx','web/src/pages/AssistantPage.tsx','web/src/providers/platformProvider.ts']:
            with self.subTest(path=name):
                entry=codeowners_for_path(name,rules)
                self.assertEqual(entry['owner'],'SHARED_CONTROLLED');self.assertEqual(entry['accounts'],['catiseyeqaq','chengege985'])

    def test_last_matching_explicit_rule_case_and_directory_boundary(self):
        rules=codeowners_rules('* @catiseyeqaq\n/a/ @chengege985\n/a/shared.py @catiseyeqaq @chengege985\n')
        self.assertEqual(codeowners_for_path('a/x.py',rules)['owner'],'B_ONLY')
        self.assertEqual(codeowners_for_path('a/shared.py',rules)['owner'],'SHARED_CONTROLLED')
        self.assertEqual(codeowners_for_path('abc/x.py',rules)['owner'],'A_ONLY')
        self.assertEqual(codeowners_for_path('A/x.py',rules)['owner'],'A_ONLY')

    def test_unknown_account_and_unsupported_pattern_fail_closed(self):
        for text in ['* @catiseyeqaq\n/a/ @AI-bot','* @catiseyeqaq\n/**/x.py @chengege985','/a/ @chengege985']:
            with self.subTest(policy=text),self.assertRaises(ValueError):codeowners_rules(text)

    def test_both_recorded_matrices_match_current_codeowners_for_every_path(self):
        # Independently evaluate explicit patterns against every stored row, not regenerated rows.
        rules=[]
        for line in (ROOT/'.github/CODEOWNERS').read_text().splitlines():
            fields=line.split('#',1)[0].split()
            if fields:rules.append((fields[0],sorted(set(x.removeprefix('@') for x in fields[1:]))))
        def expected(path):
            result=None
            for pattern,accounts in rules:
                key=pattern.removeprefix('/')
                if pattern=='*' or path==key or (key.endswith('/') and path.startswith(key)):result=(pattern,accounts)
            return result
        for filename in ['M0_FILE_MATRIX.json','CURRENT_FILE_MATRIX.json']:
            rows=json.loads((ROOT/'docs/architecture'/filename).read_text())
            for row in rows:
                with self.subTest(matrix=filename,path=row['path']):
                    pattern,accounts=expected(row['path']);target_pattern,target_accounts=expected(row['target_path'])
                    self.assertEqual(row['responsible_github_accounts'],accounts)
                    self.assertEqual(row['ownership_rule'],pattern)
                    self.assertEqual(row['target_responsible_github_accounts'],target_accounts)
                    self.assertEqual(row['target_ownership_rule'],target_pattern)
                    self.assertEqual(row['owner'],'A_ONLY' if accounts==['catiseyeqaq'] else 'B_ONLY' if accounts==['chengege985'] else 'SHARED_CONTROLLED')
                    self.assertEqual(row['shared'],len(accounts)==2)



class ReviewPythonImportRegressionTests(unittest.TestCase):
    """Independent review counterexamples; business fixtures are inert strings."""
    ai = 'engineering/src/ai_application/review_probe.py'
    business = 'engineering/src/decision/plans.py'
    gas = 'engineering/experiments/gas_m0/src/waterpipnet_gas/scoring_candidate.py'

    def checked(self, files):
        parsed = python_edges(files)
        return parsed, check(raw(files), {**graph([]), **parsed}, empty_baseline(), {})

    def blocked(self, files):
        parsed, violations = self.checked(files)
        self.assertTrue(violations, {'files': files, 'parsed': parsed})
        return parsed

    def test_gas_namespace_from_import_is_rejected(self):
        self.blocked({self.ai: 'from experiments.gas_m0.src.waterpipnet_gas import scoring_candidate', self.gas: 'VALUE=1'})

    def test_gas_namespace_dotted_import_is_rejected(self):
        self.blocked({self.ai: 'import experiments.gas_m0.src.waterpipnet_gas.scoring_candidate as scoring', self.gas: 'VALUE=1'})

    def test_resolved_gas_target_outside_src_is_still_business(self):
        files = raw({self.ai: 'x', self.gas: 'x'})
        self.assertTrue(check(files, graph([(self.ai, self.gas)]), empty_baseline(), {}))

    def test_import_module_explicit_package_positional_is_rejected(self):
        self.blocked({self.ai: "import importlib\nimportlib.import_module('.decision.plans', 'src')", self.business: 'VALUE=1'})

    def test_import_module_explicit_package_keyword_is_rejected(self):
        self.blocked({self.ai: "import importlib\nimportlib.import_module('.decision.plans', package='src')", self.business: 'VALUE=1'})

    def test_import_module_name_and_package_keywords_are_rejected(self):
        self.blocked({self.ai: "import importlib\nimportlib.import_module(name='.decision.plans', package='src')", self.business: 'VALUE=1'})

    def test_import_module_alias_with_explicit_package_is_rejected(self):
        self.blocked({self.ai: "from importlib import import_module as load\nload('.decision.plans', package='src')", self.business: 'VALUE=1'})

    def test_unknown_dynamic_package_is_rejected(self):
        self.blocked({self.ai: "import importlib\nimportlib.import_module('.decision.plans', package=user_package)", self.business: 'VALUE=1'})

    def test_relative_dynamic_import_without_package_is_rejected(self):
        self.blocked({self.ai: "import importlib\nimportlib.import_module('.decision.plans')", self.business: 'VALUE=1'})

    def test_ai_root_helper_bridge_is_rejected(self):
        self.blocked({self.ai: 'from review_helper import plans', 'engineering/review_helper.py': 'from src.decision import plans', self.business: 'VALUE=1'})

    def test_shared_contract_root_helper_bridge_is_rejected(self):
        self.blocked({'engineering/src/shared_contracts/review_probe.py': 'from review_helper import plans', 'engineering/review_helper.py': 'from src.decision import plans', self.business: 'VALUE=1'})

    def test_cuv_root_helper_to_agent_is_rejected(self):
        self.blocked({'engineering/src/cuv_application/review_probe.py': 'from review_helper import graph', 'engineering/review_helper.py': 'from src.agents import graph', 'engineering/src/agents/graph.py': 'VALUE=1'})

    def test_cuv_multiple_helpers_to_ai_is_rejected(self):
        self.blocked({'engineering/src/cuv_application/review_probe.py': 'import first_helper', 'engineering/first_helper.py': 'import second_helper', 'engineering/second_helper.py': 'from src.ai_application import ports', 'engineering/src/ai_application/ports.py': 'VALUE=1'})

    def test_gas_root_helper_to_agent_is_rejected(self):
        self.blocked({self.gas: 'from review_helper import graph', 'engineering/review_helper.py': 'from src.agents import graph', 'engineering/src/agents/graph.py': 'VALUE=1'})

    def test_gas_direct_agent_import_is_rejected(self):
        self.blocked({self.gas: 'from src.agents import graph', 'engineering/src/agents/graph.py': 'VALUE=1'})

    def test_gas_nonliteral_import_module_is_rejected(self):
        self.blocked({self.gas: 'import importlib\nimportlib.import_module(target_module)'})

    def test_gas_literal_third_party_dynamic_import_is_allowed(self):
        self.assertEqual(self.checked({self.gas: "import importlib\nimportlib.import_module('numpy')"})[1], [])

    def test_builtin_import_nonzero_or_unknown_level_is_rejected(self):
        expressions = [
            "__import__('decision.plans', {'__package__':'src'}, {}, ['plans'], 1)",
            "__import__('decision.plans', globals={'__package__':'src'}, fromlist=['plans'], level=1)",
            "__import__('decision.plans', globals={'__package__':'src'}, fromlist=['plans'], level=requested_level)",
            "__import__('decision.plans', **options)",
        ]
        for expression in expressions:
            with self.subTest(expression=expression):
                self.blocked({self.ai: expression, self.business: 'VALUE=1'})

    def test_builtin_import_default_and_zero_levels_are_allowed(self):
        expressions = [
            "__import__('json')",
            "__import__('src.shared_contracts.tool_api', {}, {}, ['tool_api'], 0)",
            "__import__('src.shared_contracts.tool_api', level=0)",
            "__import__('numpy', level=0)",
        ]
        for expression in expressions:
            with self.subTest(expression=expression):
                self.assertEqual(self.checked({self.ai: expression, 'engineering/src/shared_contracts/tool_api.py': 'VALUE=1'})[1], [])

    def test_unresolved_local_namespace_is_rejected(self):
        self.blocked({self.ai: 'import src.missing_local_module'})

    def test_same_domain_explicit_package_and_shared_are_allowed(self):
        files = {self.ai: "from src.shared_contracts import tool_api\nimport importlib\nimportlib.import_module(name='.ports', package='src.ai_application')", 'engineering/src/ai_application/ports.py': 'VALUE=1', 'engineering/src/shared_contracts/tool_api.py': 'VALUE=1'}
        parsed, violations = self.checked(files)
        self.assertEqual(violations, [])
        self.assertIn('engineering/src/ai_application/ports.py', {e['target'] for e in parsed['edges']})
        self.assertIn('engineering/src/shared_contracts/tool_api.py', {e['target'] for e in parsed['edges']})

    def test_cuv_helper_to_business_is_allowed(self):
        files = {'engineering/src/cuv_application/review_probe.py': 'from review_helper import plans', 'engineering/review_helper.py': 'from src.decision import plans', self.business: 'VALUE=1'}
        parsed, violations = self.checked(files)
        self.assertEqual(violations, [])
        self.assertGreaterEqual(len(parsed['edges']), 2)

    def test_standard_library_and_third_party_are_allowed(self):
        files = {self.ai: 'import json\nfrom pathlib import Path\nfrom pydantic import BaseModel\nfrom langgraph.graph import StateGraph'}
        self.assertEqual(self.checked(files)[1], [])


class ReviewTypeScriptExtensionRegressionTests(unittest.TestCase):
    def parsed(self, files):
        files = {'web/tsconfig.json': json.dumps({'compilerOptions': {'moduleResolution': 'Bundler', 'module': 'ESNext', 'jsx': 'react-jsx', 'allowJs': True}}), **files}
        data = raw(files)
        return data, collect_graph(ROOT, data, NODE)

    def crossing(self, suffix, specifier):
        files, parsed = self.parsed({'web/src/App.tsx': f"import {{ x }} from './features/assistant/probe{specifier}'; export const result=x;", f'web/src/features/assistant/probe{suffix}': "export { x } from '../network/b';", 'web/src/features/network/b.ts': 'export const x=1;'})
        violations = check(files, parsed, empty_baseline(), {})
        self.assertTrue(violations, parsed)
        self.assertTrue(any(e['source'].endswith('probe'+suffix) and e['target']=='web/src/features/network/b.ts' for e in parsed['edges']), parsed)

    def test_mjs_entry_resolves_mts_and_rejects_cross_domain(self):
        self.crossing('.mts', '.mjs')

    def test_cjs_entry_resolves_cts_and_rejects_cross_domain(self):
        self.crossing('.cts', '.cjs')

    def test_jsx_cross_domain_source_is_checked(self):
        self.crossing('.jsx', '.jsx')

    def test_supported_extensions_can_use_own_and_shared_modules(self):
        for suffix, specifier in [('.mts', '.mjs'), ('.cts', '.cjs'), ('.jsx', '.jsx')]:
            with self.subTest(suffix=suffix):
                files, parsed = self.parsed({'web/src/App.tsx': f"import {{x}} from './features/assistant/probe{specifier}';", f'web/src/features/assistant/probe{suffix}': "import {s} from '../../shared/value'; import {own} from './state'; export const x=s+own;", 'web/src/shared/value.ts': 'export const s=1;', 'web/src/features/assistant/state.ts': 'export const own=1;'})
                self.assertEqual(check(files, parsed, empty_baseline(), {}), [])
                self.assertTrue(any(e['source'].endswith('probe'+suffix) for e in parsed['edges']), parsed)

    def test_app_unresolved_local_entry_is_rejected(self):
        files, parsed = self.parsed({'web/src/App.tsx': "import './features/assistant/missing.mjs';"})
        self.assertTrue(check(files, parsed, empty_baseline(), {}))

    def test_external_packages_and_static_assets_remain_allowed(self):
        files, parsed = self.parsed({'web/src/App.tsx': "import React from 'react'; import './theme.css'; import image from './image.svg'; export const x=React;"})
        self.assertEqual(check(files, parsed, empty_baseline(), {}), [])


class ReviewBoundaryCliRegressionTests(unittest.TestCase):
    """Exercise the shipped CLI against isolated synthetic repositories."""
    def run_gate(self, files, expected_rejection):
        import shutil
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory(prefix='module-boundary-cli-') as temporary:
            root = Path(temporary)
            (root/'scripts').mkdir()
            (root/'docs/architecture').mkdir(parents=True)
            (root/'web').mkdir()
            (root/'.gitignore').write_text('node_modules/\n__pycache__/\n')
            (root/'web/package.json').write_text('{}')
            (root/'web/node_modules').symlink_to((ROOT/'web/node_modules').resolve(), target_is_directory=True)
            shutil.copyfile(ROOT/'scripts/parse_typescript_dependencies.mjs', root/'scripts/parse_typescript_dependencies.mjs')
            (root/'docs/architecture/FROZEN_FILES.json').write_text(json.dumps({'groups': {}}))
            (root/'docs/architecture/dependency_baseline.json').write_text(json.dumps({'base_commit': BASE_COMMIT, 'legacy_exceptions': [], 'cycles': [], 'files': {}}))
            defaults = {'web/tsconfig.json': json.dumps({'compilerOptions': {'moduleResolution': 'Bundler', 'module': 'ESNext', 'allowJs': True}})}
            for name, body in {**defaults, **files}.items():
                path=root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(body)
            subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
            command=[sys.executable, '-B', str(ROOT/'scripts/check_module_boundaries.py'), '--root', str(root), '--node', NODE]
            result=subprocess.run(command, capture_output=True, text=True)
            self.assertIn(result.returncode, (0, 1), result.stderr)
            try:
                payload=json.loads(result.stdout)
            except json.JSONDecodeError:
                self.fail(f'CLI must return structured gate result: {result.stdout}\n{result.stderr}')
            self.assertEqual(result.returncode, 1 if expected_rejection else 0, payload)
            self.assertEqual(payload['status'], 'FAIL' if expected_rejection else 'PASS', payload)
            self.assertEqual(bool(payload['violations']), expected_rejection, payload)

    def test_cli_direct_business_control_is_rejected(self):
        self.run_gate({'engineering/src/ai_application/probe.py': 'from src.decision import plans', 'engineering/src/decision/plans.py': 'VALUE=1'}, True)

    def test_cli_gas_namespace_is_rejected(self):
        self.run_gate({'engineering/src/ai_application/probe.py': 'from experiments.gas_m0.src.waterpipnet_gas import scoring_candidate', 'engineering/experiments/gas_m0/src/waterpipnet_gas/scoring_candidate.py': 'VALUE=1'}, True)

    def test_cli_explicit_package_is_rejected(self):
        self.run_gate({'engineering/src/ai_application/probe.py': "import importlib\nimportlib.import_module('.decision.plans', package='src')", 'engineering/src/decision/plans.py': 'VALUE=1'}, True)

    def test_cli_helper_bridge_is_rejected(self):
        self.run_gate({'engineering/src/ai_application/probe.py': 'from review_helper import plans', 'engineering/review_helper.py': 'from src.decision import plans', 'engineering/src/decision/plans.py': 'VALUE=1'}, True)

    def test_cli_mts_cross_domain_is_rejected(self):
        self.run_gate({'web/src/App.tsx': "import {x} from './features/assistant/probe.mjs';", 'web/src/features/assistant/probe.mts': "export {x} from '../network/b';", 'web/src/features/network/b.ts': 'export const x=1;'}, True)

    def test_cli_legitimate_shared_contract_is_allowed(self):
        self.run_gate({'engineering/src/ai_application/probe.py': 'from src.shared_contracts import tool_api', 'engineering/src/shared_contracts/tool_api.py': 'VALUE=1'}, False)


class ReviewFromlistAndParentRegressionTests(unittest.TestCase):
    """Static Python imports include fromlist children and executed parent packages."""
    ai = 'engineering/src/ai_application/fix2_probe.py'
    business = 'engineering/src/cuv_application/fix2_probe.py'
    decision = 'engineering/src/decision/plans.py'
    shared = 'engineering/src/shared_contracts/tool_api.py'
    aliases = (
        'import builtins\nbuiltins.__import__',
        'import builtins as bi\nbi.__import__',
        'from builtins import __import__ as load\nload',
    )

    def checked(self, files, baseline=None):
        parsed = python_edges(files)
        violations = check(raw(files), {**graph([]), **parsed},
                           empty_baseline() if baseline is None else baseline, {})
        self.assertEqual(parsed['parse_errors'], [])
        return parsed, violations

    def rejected(self, files, code):
        parsed, violations = self.checked(files)
        self.assertIn(code, {item['code'] for item in violations}, parsed)
        return parsed

    def parent_files(self, statement):
        return {self.business: statement,
                'engineering/review_parent/__init__.py': 'from src.ai_application import ports',
                'engineering/review_parent/child.py': 'VALUE=1',
                'engineering/src/ai_application/ports.py': 'VALUE=1'}

    def legacy_fixture(self):
        source = 'engineering/src/agents/fix2_probe.py'
        initializer = 'engineering/src/decision/__init__.py'
        files = {source: 'import src.decision.plans\n', self.decision: 'VALUE=1\n',
                 initializer: 'FLAG=1\n'}
        baseline = {'legacy_exceptions': [{'source': source, 'target': self.decision,
                     'source_sha256': digest(files[source].encode()),
                     'target_sha256': digest(files[self.decision].encode())}],
                    'files': {name: digest(body.encode()) for name, body in files.items()},
                    'cycles': []}
        return source, initializer, files, baseline

    def test_fromlist_positional_list_and_tuple_load_business_child(self):
        for members in ("['decision.plans']", "('decision.plans',)"):
            with self.subTest(members=members):
                parsed = self.rejected({self.ai: "__import__('src', {}, {}, " + members + ")",
                                        self.decision: 'VALUE=1'}, 'forbidden_import')
                self.assertIn(self.decision, {edge['target'] for edge in parsed['edges']})

    def test_fromlist_keyword_list_and_tuple_load_business_child(self):
        for members in ("['plans']", "('plans',)"):
            with self.subTest(members=members):
                self.rejected({self.ai: "__import__(name='src.decision', fromlist=" + members + ")",
                               self.decision: 'VALUE=1'}, 'forbidden_import')

    def test_dotted_fromlist_member_on_concrete_and_namespace_root(self):
        for concrete in (False, True):
            with self.subTest(concrete=concrete):
                files = {self.ai: "__import__('src', fromlist=['decision.plans'])",
                         self.decision: 'VALUE=1'}
                if concrete:
                    files['engineering/src/__init__.py'] = ''
                self.rejected(files, 'forbidden_import')

    def test_fromlist_resolves_gas_namespace_and_short_root(self):
        gas = 'engineering/experiments/gas_m0/src/waterpipnet_gas/scoring_candidate.py'
        for module in ('experiments.gas_m0.src.waterpipnet_gas', 'waterpipnet_gas'):
            with self.subTest(module=module):
                self.rejected({self.ai: f"__import__({module!r}, fromlist=['scoring_candidate'])",
                               gas: 'VALUE=1'}, 'forbidden_import')

    def test_unknown_local_fromlist_fails_closed(self):
        for members in ('requested_members', "['tool_api', requested_member]", "['tool_' + 'api']"):
            with self.subTest(members=members):
                self.rejected({self.ai: "__import__('src.shared_contracts', fromlist=" + members + ")",
                               self.shared: 'VALUE=1'}, 'unresolved_dynamic_import')

    def test_wildcard_local_fromlist_fails_closed(self):
        for members in ("['*']", "('*',)"):
            with self.subTest(members=members):
                self.rejected({self.ai: "__import__('src.shared_contracts', fromlist=" + members + ")",
                               'engineering/src/shared_contracts/__init__.py': "__all__=['tool_api']",
                               self.shared: 'VALUE=1'}, 'unresolved_dynamic_import')

    def test_missing_namespace_fromlist_child_is_not_external(self):
        self.rejected({self.ai: "__import__('src.shared_contracts', fromlist=['missing'])",
                       self.shared: 'VALUE=1'}, 'unresolved_local_import')

    def test_legal_shared_fromlist_and_static_package_attribute(self):
        for members in ("['tool_api']", "('tool_api',)", "['VALUE']"):
            with self.subTest(members=members):
                files = {self.ai: "__import__('src.shared_contracts', fromlist=" + members + ")",
                         'engineering/src/shared_contracts/__init__.py': 'VALUE=1',
                         self.shared: 'VALUE=1'}
                self.assertEqual(self.checked(files)[1], [])

    def test_default_empty_and_third_party_fromlists_remain_legal(self):
        for statement in ("__import__('src.shared_contracts')",
                          "__import__('src.shared_contracts', fromlist=[])",
                          "__import__('src.shared_contracts', fromlist=())",
                          "__import__('src.shared_contracts', level=0)",
                          "__import__('numpy', fromlist=['float64'])",
                          "import json\nfrom pathlib import Path\nfrom pydantic import BaseModel"):
            with self.subTest(statement=statement):
                self.assertEqual(self.checked({self.ai: statement, self.shared: 'VALUE=1'})[1], [])

    def test_explicit_builtin_module_and_function_aliases_cannot_bypass_fromlist(self):
        for loader in self.aliases:
            with self.subTest(loader=loader):
                self.rejected({self.ai: loader + "('src', fromlist=['decision.plans'])",
                               self.decision: 'VALUE=1'}, 'forbidden_import')

    def test_explicit_builtin_aliases_preserve_legal_shared_import(self):
        for loader in self.aliases:
            with self.subTest(loader=loader):
                parsed, violations = self.checked({self.ai: loader + "('src.shared_contracts', fromlist=('tool_api',), level=0)",
                                                   self.shared: 'VALUE=1'})
                self.assertEqual(violations, [])
                self.assertIn(self.shared, {edge['target'] for edge in parsed['edges']})

    def test_explicit_builtin_aliases_reject_nonzero_or_unknown_level(self):
        for loader in self.aliases:
            for arguments in ("('src.shared_contracts', fromlist=['tool_api'], level=1)",
                              "('src.shared_contracts', {}, {}, ['tool_api'], requested_level)",
                              "('src.shared_contracts', **options)"):
                with self.subTest(loader=loader, arguments=arguments):
                    self.rejected({self.ai: loader + arguments, self.shared: 'VALUE=1'},
                                  'unresolved_dynamic_import')

    def test_explicit_builtin_aliases_reject_unknown_or_wildcard_fromlist(self):
        for loader in self.aliases:
            for members in ('requested_members', "['*']"):
                with self.subTest(loader=loader, members=members):
                    self.rejected({self.ai: loader + "('src.shared_contracts', fromlist=" + members + ")",
                                   self.shared: 'VALUE=1'}, 'unresolved_dynamic_import')

    def test_child_import_forms_include_executed_parent_initializer(self):
        for statement in ('import review_parent.child',
                          'from review_parent.child import VALUE',
                          "import importlib\nimportlib.import_module('review_parent.child')",
                          "__import__('review_parent.child')"):
            with self.subTest(statement=statement):
                parsed = self.rejected(self.parent_files(statement), 'indirect_business_ai_import')
                parents = [edge for edge in parsed['edges'] if edge['source'] == self.business
                           and edge['target'] == 'engineering/review_parent/__init__.py']
                self.assertTrue(parents)
                self.assertEqual({edge['kind'] for edge in parents}, {'package_init'})
                self.assertEqual({edge['via_target'] for edge in parents}, {'engineering/review_parent/child.py'})

    def test_multiple_concrete_parent_initializers_are_dependencies(self):
        files = {self.business: 'import review_parent.middle.child',
                 'engineering/review_parent/__init__.py': '',
                 'engineering/review_parent/middle/__init__.py': 'from src.ai_application import ports',
                 'engineering/review_parent/middle/child.py': 'VALUE=1',
                 'engineering/src/ai_application/ports.py': 'VALUE=1'}
        parsed = self.rejected(files, 'indirect_business_ai_import')
        parents = {edge['target'] for edge in parsed['edges'] if edge['source'] == self.business
                   and edge['kind'] == 'package_init'}
        self.assertEqual(parents, {'engineering/review_parent/__init__.py',
                                   'engineering/review_parent/middle/__init__.py'})

    def test_namespace_child_keeps_concrete_grandparent_initializer(self):
        files = {self.business: 'import review_parent.middle.child',
                 'engineering/review_parent/__init__.py': 'from src.ai_application import ports',
                 'engineering/review_parent/middle/child.py': 'VALUE=1',
                 'engineering/src/ai_application/ports.py': 'VALUE=1'}
        self.rejected(files, 'indirect_business_ai_import')

    def test_namespace_target_itself_keeps_concrete_ancestor_initializer(self):
        files = {self.business: 'import review_parent.middle.empty',
                 'engineering/review_parent/__init__.py': 'from src.ai_application import ports',
                 'engineering/review_parent/middle/empty/child.py': 'VALUE=1',
                 'engineering/src/ai_application/ports.py': 'VALUE=1'}
        self.rejected(files, 'indirect_business_ai_import')

    def test_noop_parent_and_own_package_imports_remain_legal(self):
        files = self.parent_files('import review_parent.child')
        files['engineering/review_parent/__init__.py'] = ''
        parsed, violations = self.checked(files)
        self.assertEqual(violations, [])
        self.assertEqual(strongly_connected(parsed['edges']), [])
        own = {'engineering/src/ai_application/__init__.py': 'from .ports import VALUE',
               'engineering/src/ai_application/ports.py': 'from .intent import VALUE',
               'engineering/src/ai_application/intent.py': 'VALUE=1'}
        parsed, violations = self.checked(own)
        self.assertEqual(violations, [])
        self.assertEqual(strongly_connected(parsed['edges']), [])

    def test_legacy_parent_is_derived_only_from_exact_approved_child(self):
        source, initializer, files, baseline = self.legacy_fixture()
        before = json.dumps(baseline, sort_keys=True)
        parsed, violations = self.checked(files, baseline)
        self.assertEqual(violations, [])
        self.assertTrue(any(edge['source'] == source and edge['target'] == initializer
                            and edge['kind'] == 'package_init' and edge['via_target'] == self.decision
                            for edge in parsed['edges']))
        self.assertEqual(json.dumps(baseline, sort_keys=True), before)
        self.assertEqual(len(baseline['legacy_exceptions']), 1)

    def test_legacy_parent_rejects_changed_source_or_child_sha(self):
        source, initializer, files, baseline = self.legacy_fixture()
        for changed in (source, self.decision):
            with self.subTest(changed=changed):
                parsed, violations = self.checked({**files, changed: files[changed] + '# changed\n'}, baseline)
                self.assertTrue(any(item['code'] == 'forbidden_import' and item['target'] == initializer
                                    for item in violations), parsed)

    def test_legacy_parent_rejects_changed_initializer_sha(self):
        source, initializer, files, baseline = self.legacy_fixture()
        parsed, violations = self.checked({**files, initializer: '# changed\n'}, baseline)
        self.assertTrue(any(item['code'] == 'forbidden_import' and item['target'] == initializer
                            for item in violations), parsed)

    def test_legacy_parent_rejects_different_source_pair(self):
        source, initializer, files, baseline = self.legacy_fixture()
        changed = {name: body for name, body in files.items() if name != source}
        changed['engineering/src/agents/other.py'] = files[source]
        parsed, violations = self.checked(changed, baseline)
        self.assertTrue(any(item['code'] == 'forbidden_import' and item['target'] == initializer
                            for item in violations), parsed)

    def test_legacy_parent_rejects_missing_initializer_baseline(self):
        source, initializer, files, baseline = self.legacy_fixture()
        baseline['files'].pop(initializer)
        parsed, violations = self.checked(files, baseline)
        self.assertTrue(any(item['code'] == 'forbidden_import' and item['target'] == initializer
                            for item in violations), parsed)

    def test_legacy_parent_rejects_unapproved_via_target(self):
        source, initializer, files, baseline = self.legacy_fixture()
        baseline['legacy_exceptions'][0]['target'] = 'engineering/src/decision/other.py'
        parsed, violations = self.checked(files, baseline)
        self.assertTrue(any(item['code'] == 'forbidden_import' and item['target'] == initializer
                            for item in violations), parsed)


class ReviewFromlistAndParentCliRegressionTests(unittest.TestCase):
    """Keep the two review failures reproducible through the public CLI."""
    run_gate = ReviewBoundaryCliRegressionTests.run_gate

    def test_cli_fromlist_business_child_is_rejected(self):
        self.run_gate({'engineering/src/ai_application/probe.py': "__import__('src', fromlist=['decision.plans'])",
                       'engineering/src/decision/plans.py': 'VALUE=1'}, True)

    def test_cli_parent_initializer_business_to_ai_is_rejected(self):
        self.run_gate({'engineering/src/cuv_application/probe.py': 'import review_parent.child',
                       'engineering/review_parent/__init__.py': 'from src.ai_application import ports',
                       'engineering/review_parent/child.py': 'VALUE=1',
                       'engineering/src/ai_application/ports.py': 'VALUE=1'}, True)

    def test_cli_legal_shared_fromlist_and_noop_parent_are_allowed(self):
        self.run_gate({'engineering/src/ai_application/probe.py': "from builtins import __import__ as load\nload('src.shared_contracts', fromlist=['tool_api'])",
                       'engineering/src/shared_contracts/__init__.py': '',
                       'engineering/src/shared_contracts/tool_api.py': 'VALUE=1',
                       'engineering/src/cuv_application/probe.py': 'import review_parent.child',
                       'engineering/review_parent/__init__.py': '',
                       'engineering/review_parent/child.py': 'VALUE=1'}, False)



class ReviewMissingTerminalAndAncestorRegressionTests(unittest.TestCase):
    """Missing leaves still initialize parents, including a source's own parents."""
    cuv = 'engineering/src/cuv_application/fix3_probe.py'
    gas = 'engineering/experiments/gas_m0/src/waterpipnet_gas/fix3_probe.py'
    agent = 'engineering/src/agents/marker.py'
    ai = 'engineering/src/ai_application/ports.py'
    loaders = ('__import__', 'import builtins\nbuiltins.__import__',
               'import builtins as bi\nbi.__import__',
               'from builtins import __import__ as load\nload')

    def checked(self, files, baseline=None):
        parsed = python_edges(files)
        self.assertEqual(parsed['parse_errors'], [])
        return check(raw(files), {**graph([]), **parsed},
                     empty_baseline() if baseline is None else baseline, {})

    def assert_reaches_forbidden(self, files, source, target):
        violations = self.checked(files)
        self.assertTrue(any(item.get('source') == source and item.get('target') == target
                            for item in violations), violations)

    def missing_files(self, source, statement):
        return {source: statement, 'engineering/review_fromlist_helper/__init__.py': '',
                'engineering/review_fromlist_helper/branch/__init__.py': 'from src.agents import marker',
                self.agent: 'VALUE=1'}

    def ancestor_files(self, source, target, statement=None):
        if source == self.gas:
            initializer = 'engineering/experiments/__init__.py'
            helper = 'engineering/experiments/review_safe_helper.py'
            default = 'import experiments.review_safe_helper'
        else:
            initializer = 'engineering/src/__init__.py'
            helper = 'engineering/src/review_safe_helper.py'
            default = 'import src.review_safe_helper'
        module = target.removeprefix('engineering/').removesuffix('.py').replace('/', '.')
        return {source: default if statement is None else statement,
                initializer: 'import ' + module, helper: 'VALUE=1', target: 'VALUE=1'}

    def test_missing_terminal_positional_fromlist_keeps_parent_dependency(self):
        for source in (self.cuv, self.gas):
            for members in ("['branch.absent']", "('branch.absent',)"):
                with self.subTest(source=source, members=members):
                    statement = "__import__('review_fromlist_helper', {}, {}, " + members + ")"
                    self.assert_reaches_forbidden(self.missing_files(source, statement), source, self.agent)

    def test_missing_terminal_keyword_fromlist_and_builtin_bindings_keep_parent(self):
        for source in (self.cuv, self.gas):
            for loader in self.loaders:
                with self.subTest(source=source, loader=loader):
                    statement = loader + "(name='review_fromlist_helper', fromlist=('branch.absent',))"
                    self.assert_reaches_forbidden(self.missing_files(source, statement), source, self.agent)

    def test_missing_terminal_keeps_deep_parent_and_grandparent_dependencies(self):
        for source in (self.cuv, self.gas):
            for dangerous in ('outer', 'outer/inner'):
                with self.subTest(source=source, dangerous=dangerous):
                    files = {source: "__import__('review_fromlist_helper', fromlist=['outer.inner.absent'])",
                             'engineering/review_fromlist_helper/__init__.py': '',
                             'engineering/review_fromlist_helper/outer/__init__.py': '',
                             'engineering/review_fromlist_helper/outer/inner/__init__.py': '',
                             self.agent: 'VALUE=1'}
                    files['engineering/review_fromlist_helper/' + dangerous + '/__init__.py'] = (
                        'from src.agents import marker')
                    self.assert_reaches_forbidden(files, source, self.agent)

    def test_missing_terminal_traverses_namespace_intermediate(self):
        for source in (self.cuv, self.gas):
            with self.subTest(source=source):
                files = {source: "__import__('review_fromlist_helper', fromlist=['namespace.branch.absent'])",
                         'engineering/review_fromlist_helper/__init__.py': '',
                         'engineering/review_fromlist_helper/namespace/branch/__init__.py':
                             'from src.agents import marker', self.agent: 'VALUE=1'}
                self.assert_reaches_forbidden(files, source, self.agent)

    def test_missing_terminal_namespace_root_still_reports_hidden_agent(self):
        files = self.missing_files(self.cuv, "__import__('review_fromlist_helper', fromlist=['branch.absent'])")
        files.pop('engineering/review_fromlist_helper/__init__.py')
        self.assert_reaches_forbidden(files, self.cuv, self.agent)

    def test_missing_terminal_under_own_root_keeps_intermediate_dependency(self):
        files = {self.cuv: "__import__('src', fromlist=['review_branch.absent'])",
                 'engineering/src/__init__.py': '',
                 'engineering/src/review_branch/__init__.py': 'from src.agents import marker',
                 self.agent: 'VALUE=1'}
        self.assert_reaches_forbidden(files, self.cuv, self.agent)

    def test_missing_terminal_with_empty_or_constant_intermediates_is_legal(self):
        for source in (self.cuv, self.gas):
            for body in ('', 'VALUE=1'):
                with self.subTest(source=source, body=body):
                    files = self.missing_files(source, "__import__('review_fromlist_helper', fromlist=['branch.absent'])")
                    files['engineering/review_fromlist_helper/branch/__init__.py'] = body
                    self.assertEqual(self.checked(files), [])

    def test_missing_terminal_after_benign_namespace_intermediate_is_legal(self):
        files = {self.cuv: "__import__('review_fromlist_helper', fromlist=['namespace.branch.absent'])",
                 'engineering/review_fromlist_helper/__init__.py': '',
                 'engineering/review_fromlist_helper/namespace/branch/__init__.py': 'VALUE=1'}
        self.assertEqual(self.checked(files), [])

    def test_own_ancestor_agent_or_ai_dependency_is_rejected(self):
        for source in (self.cuv, self.gas):
            for target in (self.agent, self.ai):
                with self.subTest(source=source, target=target):
                    self.assert_reaches_forbidden(self.ancestor_files(source, target), source, target)

    def test_own_ancestor_is_checked_without_explicit_source_imports(self):
        for source in (self.cuv, self.gas):
            for target in (self.agent, self.ai):
                with self.subTest(source=source, target=target):
                    files = self.ancestor_files(source, target, 'VALUE=1')
                    self.assert_reaches_forbidden(files, source, target)

    def test_nested_source_keeps_shared_grandparent_initializer(self):
        source = 'engineering/src/cuv_application/nested/probe.py'
        files = self.ancestor_files(source, self.agent, 'VALUE=1')
        files['engineering/src/cuv_application/__init__.py'] = ''
        files['engineering/src/cuv_application/nested/__init__.py'] = ''
        self.assert_reaches_forbidden(files, source, self.agent)

    def test_own_ancestor_across_namespace_directory_is_checked(self):
        files = {self.cuv: 'VALUE=1', 'engineering/__init__.py': 'from src.agents import marker',
                 self.agent: 'VALUE=1'}
        self.assert_reaches_forbidden(files, self.cuv, self.agent)

    def test_unknown_loading_in_own_ancestor_fails_closed_for_business_source(self):
        for source in (self.cuv, self.gas):
            with self.subTest(source=source):
                files = self.ancestor_files(source, self.agent, 'VALUE=1')
                initializer = ('engineering/experiments/__init__.py' if source == self.gas
                               else 'engineering/src/__init__.py')
                files[initializer] = '__import__(requested_module)'
                violations = self.checked(files)
                self.assertTrue(any(item.get('source') == source
                                    and item['code'] == 'unresolved_business_dependency'
                                    for item in violations), violations)

    def test_empty_and_constant_own_ancestors_are_legal_for_all_domains(self):
        for body in ('', 'VALUE=1'):
            with self.subTest(body=body):
                files = {self.cuv: 'VALUE=1', self.gas: 'VALUE=1', self.ai: 'VALUE=1',
                         'engineering/src/shared_contracts/types.py': 'VALUE=1',
                         'engineering/__init__.py': body, 'engineering/src/__init__.py': body,
                         'engineering/experiments/__init__.py': body}
                self.assertEqual(self.checked(files), [])

    def test_own_ancestor_reexport_does_not_invent_an_import_cycle(self):
        for source, initializer, reexport in (
            (self.cuv, 'engineering/src/__init__.py', 'from .cuv_application.fix3_probe import VALUE'),
            (self.gas, 'engineering/experiments/__init__.py',
             'from .gas_m0.src.waterpipnet_gas.fix3_probe import VALUE'),
        ):
            with self.subTest(source=source):
                self.assertEqual(self.checked({source: 'VALUE=1', initializer: reexport}), [])

    def test_shared_ancestor_does_not_weaken_exact_legacy_parent_hashes(self):
        source = 'engineering/src/agents/fix3_probe.py'
        target = 'engineering/src/decision/plans.py'
        parent = 'engineering/src/decision/__init__.py'
        files = {source: 'import src.decision.plans\n', target: 'VALUE=1\n',
                 parent: 'FLAG=1\n', 'engineering/src/__init__.py': '"""Common root."""\n'}
        baseline = {'legacy_exceptions': [{'source': source, 'target': target,
                    'source_sha256': digest(files[source].encode()),
                    'target_sha256': digest(files[target].encode())}],
                    'files': {name: digest(body.encode()) for name, body in files.items()}, 'cycles': []}
        approved = json.dumps(baseline, sort_keys=True)
        self.assertEqual(self.checked(files, baseline), [])
        for changed in (source, target, parent):
            with self.subTest(changed=changed):
                violations = self.checked({**files, changed: files[changed] + '# changed\n'}, baseline)
                self.assertTrue(any(item['code'] == 'forbidden_import'
                                    and item.get('target') == parent for item in violations), violations)
        missing = json.loads(approved)
        missing['files'].pop(parent)
        self.assertTrue(self.checked(files, missing))
        self.assertEqual(json.dumps(baseline, sort_keys=True), approved)


class ReviewMissingTerminalAndAncestorCliRegressionTests(unittest.TestCase):
    """Exercise both reviewed failures through the shipped command line."""
    run_gate = ReviewBoundaryCliRegressionTests.run_gate
    cuv = ReviewMissingTerminalAndAncestorRegressionTests.cuv
    gas = ReviewMissingTerminalAndAncestorRegressionTests.gas
    agent = ReviewMissingTerminalAndAncestorRegressionTests.agent
    missing_files = ReviewMissingTerminalAndAncestorRegressionTests.missing_files
    ancestor_files = ReviewMissingTerminalAndAncestorRegressionTests.ancestor_files

    def test_cli_missing_terminal_fromlist_cuv_and_gas_are_rejected(self):
        for source in (self.cuv, self.gas):
            with self.subTest(source=source):
                self.run_gate(self.missing_files(source,
                    "__import__('review_fromlist_helper', fromlist=['branch.absent'])"), True)

    def test_cli_own_ancestor_without_source_import_cuv_and_gas_are_rejected(self):
        for source in (self.cuv, self.gas):
            with self.subTest(source=source):
                self.run_gate(self.ancestor_files(source, self.agent, 'VALUE=1'), True)

    def test_cli_missing_terminal_with_benign_intermediate_is_allowed(self):
        files = self.missing_files(self.cuv,
                    "__import__('review_fromlist_helper', fromlist=['branch.absent'])")
        files['engineering/review_fromlist_helper/branch/__init__.py'] = 'VALUE=1'
        self.run_gate(files, False)

    def test_cli_benign_ancestor_self_reexport_is_allowed(self):
        self.run_gate({self.cuv: 'VALUE=1',
                       'engineering/src/__init__.py': 'from .cuv_application.fix3_probe import VALUE'}, False)



class ReviewAncestorApplicationAndModulePrefixTests(unittest.TestCase):
    """Apply initialization reachability to module prefixes and protected callers."""
    checked = ReviewMissingTerminalAndAncestorRegressionTests.checked
    assert_reaches_forbidden = ReviewMissingTerminalAndAncestorRegressionTests.assert_reaches_forbidden
    cuv = ReviewMissingTerminalAndAncestorRegressionTests.cuv
    gas = ReviewMissingTerminalAndAncestorRegressionTests.gas
    agent = ReviewMissingTerminalAndAncestorRegressionTests.agent

    def test_missing_terminal_after_plain_module_keeps_executed_dependency(self):
        for source in (self.cuv, self.gas):
            with self.subTest(source=source):
                files = {source: "__import__('review_fromlist_helper', fromlist=['branch.absent'])",
                         'engineering/review_fromlist_helper/__init__.py': '',
                         'engineering/review_fromlist_helper/branch.py': 'from src.agents import marker',
                         self.agent: 'VALUE=1'}
                self.assert_reaches_forbidden(files, source, self.agent)

    def test_ai_and_shared_own_ancestor_cannot_import_private_business(self):
        target = 'engineering/src/decision/plans.py'
        for source in ('engineering/src/ai_application/fix3_probe.py',
                       'engineering/src/shared_contracts/fix3_probe.py'):
            with self.subTest(source=source):
                files = {source: 'VALUE=1', target: 'VALUE=1',
                         'engineering/src/__init__.py': 'from src.decision import plans'}
                self.assert_reaches_forbidden(files, source, target)

    def test_ai_and_shared_own_ancestor_unknown_loading_fails_closed(self):
        for source in ('engineering/src/ai_application/fix3_probe.py',
                       'engineering/src/shared_contracts/fix3_probe.py'):
            with self.subTest(source=source):
                violations = self.checked({source: 'VALUE=1',
                    'engineering/src/__init__.py': '__import__(requested_module)'})
                self.assertTrue(any(item.get('source') == source and 'unresolved' in item['code']
                                    for item in violations), violations)



class ReviewRelativeReexportRegressionTests(unittest.TestCase):
    # Package containers are not explicit self-import cycles.
    roots = ('engineering/src/cuv_application/review_exports',
             'engineering/experiments/gas_m0/src/waterpipnet_gas/review_exports',
             'engineering/src/ai_application/review_exports',
             'engineering/src/shared_contracts/review_exports')

    def checked(self, files, baseline=None):
        parsed = python_edges(files)
        self.assertEqual(parsed['parse_errors'], [])
        return parsed, check(raw(files), {**graph([]), **parsed},
                             empty_baseline() if baseline is None else baseline, {})

    def assert_legal_children(self, folder, statement, children):
        initializer = folder + '/__init__.py'
        files = {initializer: statement, **{folder + '/' + name: body for name, body in children.items()}}
        parsed, violations = self.checked(files)
        self.assertEqual(violations, [])
        self.assertEqual(strongly_connected(parsed['edges']), [])
        pairs = {(e['source'], e['target']) for e in parsed['edges']}
        self.assertNotIn((initializer, initializer), pairs)
        for name in children:
            self.assertIn((initializer, folder + '/' + name), pairs)

    def test_relative_leaf_reexport_in_each_domain_is_legal(self):
        for folder in self.roots:
            with self.subTest(folder=folder):
                self.assert_legal_children(folder, 'from . import leaf', {'leaf.py': 'VALUE=1'})

    def test_relative_aliased_leaf_reexport_is_legal(self):
        for folder in self.roots:
            with self.subTest(folder=folder):
                self.assert_legal_children(folder, 'from . import leaf as public_leaf', {'leaf.py': 'VALUE=1'})

    def test_relative_multiple_members_keep_every_child_dependency(self):
        for folder in self.roots:
            with self.subTest(folder=folder):
                self.assert_legal_children(folder, 'from . import left, right as public_right',
                                           {'left.py': 'VALUE=1', 'right.py': 'VALUE=2'})

    def test_relative_child_package_reexport_is_legal(self):
        for folder in self.roots:
            with self.subTest(folder=folder):
                self.assert_legal_children(folder, 'from . import child', {'child/__init__.py': 'VALUE=1'})

    def test_nested_relative_package_reexports_do_not_invent_cycles(self):
        folder = self.roots[0]
        files = {folder + '/__init__.py': 'from . import child',
                 folder + '/child/__init__.py': 'from . import leaf as public_leaf',
                 folder + '/child/leaf.py': 'VALUE=1'}
        parsed, violations = self.checked(files)
        self.assertEqual(violations, [])
        self.assertEqual(strongly_connected(parsed['edges']), [])
        pairs = {(e['source'], e['target']) for e in parsed['edges']}
        self.assertIn((folder + '/__init__.py', folder + '/child/__init__.py'), pairs)
        self.assertIn((folder + '/child/__init__.py', folder + '/child/leaf.py'), pairs)

    def test_existing_nullable_attributes_and_leaf_may_be_reexported(self):
        folder = self.roots[0]
        self.assert_legal_children(folder, 'VALUE=None\nfrom . import VALUE as public_value, leaf',
                                   {'leaf.py': 'VALUE=1'})
        parsed, violations = self.checked({folder + '/__init__.py': 'VALUE=None\nfrom . import VALUE'})
        self.assertEqual(violations, [])
        self.assertEqual(parsed['edges'], [])

    def test_regular_module_still_depends_on_its_relative_package(self):
        folder = self.roots[0]; source = folder + '/consumer.py'; initializer = folder + '/__init__.py'
        parsed, violations = self.checked({source: 'from . import leaf', initializer: '', folder + '/leaf.py': 'VALUE=1'})
        self.assertEqual(violations, [])
        self.assertTrue(any(e['source'] == source and e['target'] == initializer and e['kind'] == 'from'
                            for e in parsed['edges']))

    def test_business_reexported_leaf_cannot_hide_agent(self):
        target = 'engineering/src/agents/review_marker.py'
        for folder in self.roots[:2]:
            with self.subTest(folder=folder):
                initializer = folder + '/__init__.py'
                parsed, violations = self.checked({initializer: 'from . import leaf',
                    folder + '/leaf.py': 'from src.agents import review_marker', target: 'VALUE=1'})
                self.assertTrue(any(v['code'] == 'indirect_business_ai_import' and v.get('source') == initializer
                                    and v.get('target') == target for v in violations), violations)

    def test_multiple_members_with_one_forbidden_leaf_are_rejected(self):
        folder = self.roots[0]; target = 'engineering/src/agents/review_marker.py'
        parsed, violations = self.checked({folder + '/__init__.py': 'from . import good, bad as public_bad',
            folder + '/good.py': 'VALUE=1', folder + '/bad.py': 'import src.agents.review_marker', target: 'VALUE=1'})
        self.assertTrue(any(v['code'] == 'indirect_business_ai_import' and v.get('target') == target
                            for v in violations), violations)
        self.assertIn(folder + '/good.py', {e['target'] for e in parsed['edges']})
        self.assertIn(folder + '/bad.py', {e['target'] for e in parsed['edges']})

    def test_ai_and_shared_reexport_leaf_cannot_import_private_business(self):
        target = 'engineering/src/decision/review_plan.py'
        for folder in self.roots[2:]:
            with self.subTest(folder=folder):
                leaf = folder + '/leaf.py'
                parsed, violations = self.checked({folder + '/__init__.py': 'from . import leaf as public_leaf',
                    leaf: 'import src.decision.review_plan', target: 'VALUE=1'})
                self.assertTrue(any(v['code'] == 'forbidden_import' and v.get('source') == leaf
                                    and v.get('target') == target for v in violations), violations)

    def test_reexported_leaf_unknown_loading_still_fails_closed(self):
        for folder in self.roots:
            with self.subTest(folder=folder):
                leaf = folder + '/leaf.py'
                parsed, violations = self.checked({folder + '/__init__.py': 'from . import leaf',
                    leaf: '__import__(requested_module)'})
                self.assertTrue(any(v['code'] == 'unresolved_dynamic_import' and v.get('source') == leaf
                                    for v in violations), violations)

    def test_explicit_absolute_self_import_is_still_a_cycle(self):
        folder = self.roots[0]; initializer = folder + '/__init__.py'
        parsed, violations = self.checked({initializer: 'import src.cuv_application.review_exports'})
        self.assertEqual(strongly_connected(parsed['edges']), [[initializer]])
        self.assertTrue(any(v['code'] == 'new_or_changed_cycle' for v in violations))

    def test_explicit_absolute_from_self_import_is_still_a_cycle(self):
        folder = self.roots[0]; initializer = folder + '/__init__.py'
        parsed, violations = self.checked({initializer: 'VALUE=1\nfrom src.cuv_application.review_exports import VALUE'})
        self.assertEqual(strongly_connected(parsed['edges']), [[initializer]])
        self.assertTrue(any(v['code'] == 'new_or_changed_cycle' for v in violations))

    def test_reexport_does_not_hide_explicit_two_module_cycle(self):
        folder = self.roots[0]; leaf = folder + '/leaf.py'; peer = folder + '/peer.py'
        parsed, violations = self.checked({folder + '/__init__.py': 'from . import leaf',
            leaf: 'import src.cuv_application.review_exports.peer', peer: 'import src.cuv_application.review_exports.leaf'})
        self.assertIn(sorted([leaf, peer]), strongly_connected(parsed['edges']))
        self.assertTrue(any(v['code'] == 'new_or_changed_cycle' and v['members'] == sorted([leaf, peer])
                            for v in violations))

    def test_explicit_child_back_import_to_initializer_still_forms_cycle(self):
        folder = self.roots[0]; initializer = folder + '/__init__.py'; leaf = folder + '/leaf.py'
        parsed, violations = self.checked({initializer: 'from . import leaf',
            leaf: 'import src.cuv_application.review_exports'})
        self.assertIn(sorted([initializer, leaf]), strongly_connected(parsed['edges']))
        self.assertTrue(any(v['code'] == 'new_or_changed_cycle' for v in violations))

    def test_legacy_relative_reexport_keeps_source_child_and_parent_sha_pins(self):
        source = 'engineering/src/agents/review_legacy.py'; parent = 'engineering/src/decision/__init__.py'
        target = 'engineering/src/decision/review_plan.py'
        files = {source: 'import src.decision.review_plan', parent: 'from . import review_plan', target: 'VALUE=1'}
        baseline = {'legacy_exceptions': [{'source': source, 'target': target,
                    'source_sha256': digest(files[source].encode()), 'target_sha256': digest(files[target].encode())}],
                    'files': {name: digest(body.encode()) for name, body in files.items()}, 'cycles': []}
        baseline_before = json.dumps(baseline, sort_keys=True)
        self.assertEqual(self.checked(files, baseline)[1], [])
        for name in (source, parent, target):
            with self.subTest(changed=name):
                parsed, violations = self.checked({**files, name: files[name] + '\n# changed'}, baseline)
                self.assertTrue(any(v['code'] == 'forbidden_import' for v in violations), violations)
        self.assertEqual(json.dumps(baseline, sort_keys=True), baseline_before)


    def test_wildcard_reexport_remains_outside_the_legal_container_exception(self):
        folder = self.roots[0]; initializer = folder + '/__init__.py'
        for files in ({initializer: 'from . import *'},
                      {initializer: "__all__=['leaf']\nfrom . import *",
                       folder + '/leaf.py': 'import src.cuv_application.review_exports'}):
            with self.subTest(files=files):
                parsed, violations = self.checked(files)
                self.assertIn([initializer], strongly_connected(parsed['edges']))
                self.assertTrue(any(v['code'] == 'new_or_changed_cycle' for v in violations))



class ReviewRelativeReexportCliTests(unittest.TestCase):
    # Public entrypoint controls complement the graph assertions above.
    run_gate = ReviewBoundaryCliRegressionTests.run_gate
    folder = 'engineering/src/cuv_application/review_exports'

    def test_cli_relative_leaf_reexport_is_allowed(self):
        self.run_gate({self.folder + '/__init__.py': 'from . import leaf', self.folder + '/leaf.py': 'VALUE=1'}, False)

    def test_cli_alias_and_multiple_members_are_allowed(self):
        for statement in ('from . import leaf as public_leaf', 'from . import leaf, other as public_other'):
            with self.subTest(statement=statement):
                self.run_gate({self.folder + '/__init__.py': statement,
                    self.folder + '/leaf.py': 'VALUE=1', self.folder + '/other.py': 'VALUE=2'}, False)

    def test_cli_nested_relative_reexports_are_allowed(self):
        self.run_gate({self.folder + '/__init__.py': 'from . import child',
            self.folder + '/child/__init__.py': 'from . import leaf', self.folder + '/child/leaf.py': 'VALUE=1'}, False)

    def test_cli_reexported_leaf_to_agent_is_rejected(self):
        self.run_gate({self.folder + '/__init__.py': 'from . import good, leaf as public_leaf',
            self.folder + '/good.py': 'VALUE=1', self.folder + '/leaf.py': 'from src.agents import review_marker',
            'engineering/src/agents/review_marker.py': 'VALUE=1'}, True)

    def test_cli_explicit_cycle_is_rejected(self):
        self.run_gate({self.folder + '/__init__.py': 'from . import leaf',
            self.folder + '/leaf.py': 'import src.cuv_application.review_exports.peer',
            self.folder + '/peer.py': 'import src.cuv_application.review_exports.leaf'}, True)

if __name__ == '__main__':
    unittest.main(verbosity=2)
