# Chorus Pivot Plan

## Contract-First Execution Harness for AI Coding Agents

**Version:** 1.0  
**Project:** Chorus  
**Direction:** Contract-first software-change execution harness  
**Core positioning:** PR trust layer for AI-generated code changes

---

## 1. Executive Summary

Chorus should no longer be positioned as a generic AI agent harness, a SWE-bench wrapper, a model router, or a generic security/governance layer.

The stronger direction is:

> **Chorus is a contract-first execution harness for AI coding agents. It turns a coding task into an enforceable engineering contract, runs an AI coding agent inside that contract, verifies the result through tests/policies/diff checks, and produces a PR-ready proof package.**

This means Chorus is not trying to replace Cursor, Claude Code, GitHub Copilot, Aider, OpenHands, or SWE-agent.

Instead, Chorus acts as the **discipline layer** around coding agents.

Cursor gives the agent intelligence.  
Claude Code gives the agent tools.  
Copilot gives the agent a cloud environment.  
OpenHands gives an open agent platform.  

**Chorus gives the software change a contract, a sandbox, file boundaries, required tests, a budget, and proof.**

---

## 2. Why We Are Changing Direction

The previous direction had a weak point:

```text
Task text
→ model call
→ patch
→ benchmark judge
→ report
```

That is not enough.

The previous SWE-bench path proved that Chorus had a measurement/reporting layer, but it lacked a real repo-aware execution harness. The model could produce an empty patch or fail because it did not have enough structured access to inspect files, edit code, run tests, and repair based on feedback.

The new direction should be:

```text
Task / issue / failing test
→ task classifier
→ contract generator
→ policy compiler
→ sandbox/worktree setup
→ agent execution loop
→ tool proxy enforcement
→ tests + diff verification
→ PR proof package
→ optional reliability comparison
```

The new product question is not:

> Did this model solve SWE-bench?

The new product question is:

> Did this AI-generated software change obey the engineering contract?

---

## 3. Product Thesis

The world already has strong coding agents.

But teams still have these problems:

```text
The agent touches too many files.
The agent changes risky code.
The agent skips tests.
The agent passes one test but breaks another.
The agent makes a messy PR.
The agent spends too much money on simple tasks.
The agent cannot prove it reproduced the bug.
The reviewer does not know what to trust.
```

So Chorus should not say:

> We make agents smarter.

Chorus should say:

> **We make agent-generated code changes bounded, verified, and reviewable.**

---

## 4. Final Positioning

Do **not** pitch Chorus as:

```text
AI harness
agent governance
SWE-bench runner
model router
coding agent
generic AI security system
```

Pitch Chorus as:

> **Chorus is the contract and proof layer for AI-generated code changes.**

Or:

> **Chorus gives coding agents a job spec, sandbox, budget, file permissions, required tests, and PR proof package.**

Or:

> **Chorus is Contract CI for AI coding agents.**

---

## 5. Core Product Definition

Chorus should support four main workflows:

```bash
murmur fix-test --cmd "pytest tests/checkout/test_total.py -q"

murmur fix-issue --issue issue.md

murmur verify-pr --base main --head agent/fix-checkout

murmur eval-contract --task .murmur/tasks/bug_001.yaml --n 5
```

The first MVP command should be:

```bash
murmur fix-test --cmd "pytest tests/checkout/test_total.py -q" --budget 0.50
```

Why start with failing tests?

Because failing tests provide a clear, objective success condition.

---

## 6. Core Product Loop

Most coding agents follow this loop:

```text
Prompt
→ Agent acts
→ Maybe tests run
→ Human reviews
```

Chorus should use a different loop:

```text
Task
→ Contract
→ Agent acts within contract
→ Verification
→ Human reviews proof
```

The contract is the product.

---

## 7. System Architecture

Think of Chorus as two layers.

### 7.1 Control Plane

The control plane is responsible for decision-making and enforcement.

```text
CONTROL PLANE
- understands the task
- creates the contract
- chooses the workflow
- enforces policy
- records events
- produces proof/report
```

### 7.2 Data Plane

The data plane is where execution happens.

```text
DATA PLANE
- isolated worktree
- sandbox
- tool calls
- model calls
- file edits
- test execution
- diff generation
```

### 7.3 High-Level Architecture Diagram

```text
┌─────────────────────────────────────────────────────────────┐
│                         CLI / API                           │
│          fix-test | fix-issue | verify-pr | eval             │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                    Task Intake Layer                         │
│  Parse issue, failing command, PR diff, repo metadata          │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                    Contract Compiler                         │
│  Task type, risk level, allowed files, tests, budget, gates    │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                    Run Orchestrator                          │
│  State machine: prepare → reproduce → localize → patch → verify│
└───────────────┬───────────────────────────────┬─────────────┘
                │                               │
┌───────────────▼───────────────┐   ┌──────────▼──────────────┐
│        Agent Adapter           │   │       Policy Engine      │
│  DeepSeek / OpenAI / Claude    │   │  allow/deny/escalate     │
│  Aider / OpenHands later       │   │  file/tool/test/budget   │
└───────────────┬───────────────┘   └──────────┬──────────────┘
                │                              │
┌───────────────▼──────────────────────────────▼──────────────┐
│                         Tool Proxy                           │
│ list/read/search/edit/apply_patch/run_test/shell/final_patch  │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                  Sandbox / Git Worktree                      │
│ isolated repo copy, command timeout, filesystem boundaries    │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                    Verification Layer                        │
│ tests, diff policy, forbidden files, risk checks, proof       │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                    PR Proof Reporter                         │
│ report.md, report.html, event log, final diff, review packet  │
└─────────────────────────────────────────────────────────────┘
```

---

## 8. The murmur Contract

A murmur contract is a typed YAML file that can be generated automatically, edited by a human, and enforced by the runtime.

Example:

```yaml
version: 1

task:
  id: checkout-total-flaky-test
  type: failing_test
  title: Fix checkout total calculation test
  command: "pytest tests/checkout/test_total.py -q"

repo:
  root: "."
  base_ref: "main"
  worktree_mode: "isolated"

risk:
  level: medium
  reason:
    - "Touches checkout logic"
    - "Could affect payment-adjacent behavior"

budget:
  max_cost_usd: 0.50
  max_model_calls: 12
  max_tool_calls: 80
  max_runtime_seconds: 600

files:
  allow_read:
    - "src/checkout/**"
    - "tests/checkout/**"
    - "pyproject.toml"
    - "package.json"
  allow_edit:
    - "src/checkout/**"
    - "tests/checkout/**"
  deny_read:
    - ".env"
    - "secrets/**"
  deny_edit:
    - "src/auth/**"
    - "src/payments/**"
    - "migrations/**"
    - ".github/workflows/**"

tools:
  allow:
    - list_files
    - search
    - read_file
    - apply_patch
    - run_test
    - git_diff
  deny:
    - network
    - install_dependency
    - delete_file
    - push_branch

required_proof:
  reproduce_before_fix: true
  target_test_passes_after_fix: true
  related_tests:
    - "pytest tests/checkout -q"
  static_checks:
    - "python -m compileall src"
  forbidden_files_unchanged: true
  max_files_changed: 3
  max_diff_lines: 200

escalation:
  allow_stronger_model_after:
    - "two_failed_patch_attempts"
    - "localization_confidence_below_0.5"
  require_human_approval_if:
    - "forbidden_file_requested"
    - "dependency_change_requested"
    - "migration_file_touched"
```

---

## 9. Main Engineering Principles

### 9.1 Contract First, Agent Second

The agent should not start coding immediately.

Chorus should first define:

```text
What files can be read?
What files can be edited?
What commands must pass?
What budget is allowed?
What risk level is this?
What proof is required?
```

Only then should the agent act.

---

### 9.2 Fail Closed

If Chorus is unsure whether an action is allowed, it should deny the action or ask for human approval.

Do not let the model decide critical permissions.

---

### 9.3 Deterministic Core, Probabilistic Edge

The LLM can propose actions.

The deterministic harness decides:

```text
whether file access is allowed
whether budget is exceeded
whether tests passed
whether diff policy passed
whether forbidden files changed
```

Never rely on prompting for critical enforcement.

---

### 9.4 Least Privilege

Each task gets the smallest permission set needed.

Examples:

```text
A docs task should not read .env.
A frontend styling task should not edit migrations.
A failing test task should not install dependencies unless the contract allows it.
```

---

### 9.5 Reproducibility

Every run should be reproducible.

Store:

```text
contract
model
temperature
tool calls
test commands
diff
events
final report
```

---

### 9.6 Separation of Concerns

Do not mix:

```text
policy logic
tool execution
model calling
report generation
test verification
```

Each should be a separate module.

---

### 9.7 Minimal Diff

Reward focused patches.

Warn or block when:

```text
too many files changed
large unrelated edits
formatting-only noise
dependency changes
generated files changed
```

---

### 9.8 Human Review Is a Feature

Do not aim for fully autonomous merge in the MVP.

Aim for:

```text
agent writes
Chorus verifies
human reviews proof
```

---

## 10. Design Patterns

### 10.1 Hexagonal Architecture

Use hexagonal architecture so the core logic does not depend on one model provider, one CLI, one sandbox, or one report format.

```text
Core domain:
- Contract
- Run
- Event
- PolicyDecision
- ToolCall
- VerificationResult

Ports:
- ModelProvider
- ToolExecutor
- Sandbox
- EventStore
- Reporter
- GitProvider

Adapters:
- DeepSeekProvider
- OpenAIProvider
- ClaudeProvider
- LocalShellSandbox
- DockerSandbox
- HtmlReporter
- JsonReporter
- GitCliAdapter
```

Suggested folders:

```text
murmur/
  domain/
  application/
  ports/
  adapters/
  cli/
  report/
```

---

### 10.2 Strategy Pattern

Use Strategy for different workflows.

```python
class ExecutionStrategy(Protocol):
    def build_contract(self, task: Task) -> Contract: ...
    def next_step(self, state: RunState) -> Step: ...
```

Strategies:

```text
FailingTestStrategy
IssueFixStrategy
VerifyPRStrategy
DependencyUpgradeStrategy
RefactorSafeStrategy
GenerateTestsStrategy
```

Why?

Different tasks need different workflows.

```text
A failing test needs reproduction.
A dependency upgrade needs install and lockfile rules.
A refactor needs behavior-preservation checks.
A PR verification task needs diff analysis, not patch generation.
```

---

### 10.3 State Machine Pattern

The agent run should not be a loose while-loop.

Use a controlled state machine.

```text
CREATED
→ PREPARED
→ REPRODUCING_FAILURE
→ LOCALIZING
→ CONTRACT_REVIEW
→ PATCHING
→ VERIFYING_TARGET
→ VERIFYING_RELATED
→ DIFF_POLICY_CHECK
→ PROOF_BUILDING
→ PASSED / FAILED / BLOCKED / ESCALATED
```

Example:

```python
class RunState(Enum):
    CREATED = "created"
    PREPARED = "prepared"
    REPRODUCING_FAILURE = "reproducing_failure"
    LOCALIZING = "localizing"
    PATCHING = "patching"
    VERIFYING = "verifying"
    POLICY_CHECKING = "policy_checking"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
```

Benefits:

```text
debuggable behavior
testable transitions
clear failure states
clean reporting
```

---

### 10.4 Chain of Responsibility for Policy Checks

Every tool call goes through a chain of policy checks.

```text
Tool request
→ budget check
→ file permission check
→ command safety check
→ network check
→ risk escalation check
→ execute or deny
```

Code shape:

```python
class PolicyRule(Protocol):
    def evaluate(self, request: ToolRequest, context: RunContext) -> PolicyDecision: ...
```

Rules:

```text
BudgetRule
FileReadRule
FileEditRule
ShellCommandRule
NetworkRule
SecretAccessRule
DiffSizeRule
DependencyChangeRule
RiskyPathRule
```

This keeps policy modular and easy to extend.

---

### 10.5 Adapter Pattern

Use adapters for models, agents, sandboxes, and reporters.

```python
class AgentAdapter(Protocol):
    def next_action(self, observation: Observation, context: AgentContext) -> AgentAction: ...
```

Adapters:

```text
ChorusLiteAgentAdapter
AiderAdapter
OpenHandsAdapter
ClaudeCodeAdapter
DeepSeekProvider
OpenAIProvider
AnthropicProvider
```

Start with `ChorusLiteAgentAdapter`.

---

### 10.6 Command Pattern

Represent every agent action as a command object.

```python
@dataclass
class ReadFileCommand:
    path: str

@dataclass
class ApplyPatchCommand:
    patch: str

@dataclass
class RunTestCommand:
    command: str
```

Benefits:

```text
easy logging
easy replay
easy policy enforcement
easy dry-run mode
easy testing
```

---

### 10.7 Event Sourcing

Every important action becomes an append-only event.

Example event:

```json
{
  "event_id": "evt_001",
  "run_id": "run_abc",
  "timestamp": "2026-06-03T22:00:00Z",
  "type": "tool_call_requested",
  "payload": {
    "tool": "read_file",
    "path": "src/checkout/calculate_total.py"
  }
}
```

Event types:

```text
run_started
contract_generated
contract_approved
tool_call_requested
tool_call_allowed
tool_call_denied
file_read
patch_applied
test_started
test_finished
model_call_started
model_call_finished
budget_updated
policy_violation
verification_passed
verification_failed
proof_generated
run_finished
```

Benefits:

```text
auditability
debugging
replay
HTML timeline
future dashboard
reliability comparison
```

---

### 10.8 Template Method Pattern

Each workflow follows the same skeleton, while task-specific steps differ.

```python
class BaseWorkflow:
    def run(self):
        self.prepare()
        self.build_contract()
        self.execute()
        self.verify()
        self.report()
```

Example:

```python
class FixTestWorkflow(BaseWorkflow):
    def verify(self):
        run_original_failing_test()
        run_related_tests()
        check_diff_policy()
```

---

### 10.9 Specification Pattern

Use specifications for contract satisfaction.

```python
class MaxFilesChangedSpec:
    def is_satisfied_by(self, diff: Diff) -> bool: ...

class RequiredTestPassedSpec:
    def is_satisfied_by(self, result: VerificationResult) -> bool: ...
```

This keeps verification clean.

---

## 11. Proposed Folder Architecture

```text
murmur/
  __init__.py

  cli/
    main.py
    commands/
      fix_test.py
      fix_issue.py
      verify_pr.py
      eval_contract.py

  domain/
    contract.py
    task.py
    run.py
    event.py
    policy.py
    tool.py
    diff.py
    verification.py
    proof.py
    errors.py

  application/
    workflows/
      base.py
      fix_test.py
      fix_issue.py
      verify_pr.py
    orchestrator.py
    contract_compiler.py
    task_classifier.py
    localization.py
    verifier.py
    proof_builder.py
    budget_manager.py

  ports/
    model_provider.py
    agent_adapter.py
    tool_executor.py
    sandbox.py
    event_store.py
    reporter.py
    git_provider.py

  adapters/
    models/
      deepseek.py
      openai_compatible.py
      anthropic.py
    agents/
      chorus_lite.py
      aider.py
      openhands.py
    tools/
      filesystem.py
      ripgrep.py
      patch.py
      shell.py
      git.py
    sandboxes/
      local_worktree.py
      docker.py
    event_stores/
      jsonl.py
      sqlite.py
    reporters/
      markdown.py
      html.py
      json.py

  policy/
    engine.py
    rules/
      budget.py
      file_access.py
      shell_command.py
      network.py
      diff_size.py
      secret_access.py
      dependency_change.py

  schemas/
    contract.schema.json
    event.schema.json
    report.schema.json

  tests/
    unit/
    integration/
    fixtures/
```

---

## 12. Data Model

### 12.1 Contract

```python
@dataclass(frozen=True)
class Contract:
    version: int
    task: TaskSpec
    repo: RepoSpec
    risk: RiskSpec
    budget: BudgetSpec
    files: FilePolicy
    tools: ToolPolicy
    required_proof: ProofSpec
    escalation: EscalationPolicy
```

---

### 12.2 Run

```python
@dataclass
class Run:
    id: str
    task_id: str
    contract_id: str
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None
    cost_usd: Decimal
    model_calls: int
    tool_calls: int
    attempts: int
```

---

### 12.3 ToolRequest

```python
@dataclass
class ToolRequest:
    run_id: str
    tool_name: str
    args: dict
    requested_by: str
    state: RunState
```

---

### 12.4 PolicyDecision

```python
@dataclass
class PolicyDecision:
    decision: Literal["allow", "deny", "ask_human", "escalate"]
    rule_id: str
    reason: str
```

---

### 12.5 VerificationResult

```python
@dataclass
class VerificationResult:
    passed: bool
    target_test_passed: bool
    related_tests_passed: bool
    forbidden_files_touched: list[str]
    changed_files: list[str]
    diff_lines: int
    failures: list[str]
```

---

## 13. MVP Workflow: `murmur fix-test`

### 13.1 User Command

```bash
murmur fix-test --cmd "pytest tests/checkout/test_total.py -q" --budget 0.50
```

### 13.2 Execution Steps

```text
1. Developer runs the command.

2. Chorus creates an isolated git worktree.

3. Chorus runs the failing command before any AI edit.

4. If the test does not fail, Chorus stops:
   "Cannot prove fix because failure was not reproduced."

5. Chorus parses failure output.

6. Chorus localizes likely files using:
   - traceback
   - test names
   - ripgrep
   - import graph later

7. Chorus generates a contract:
   - allowed files
   - denied files
   - required tests
   - max budget
   - max diff size

8. Chorus starts the agent loop.

9. Agent can only use tools through the Tool Proxy.

10. Tool Proxy asks Policy Engine before execution.

11. Agent applies a patch.

12. Chorus runs the target test.

13. If the test fails, the agent receives feedback and retries.

14. If target test passes, Chorus runs related tests.

15. Chorus checks diff policy.

16. Chorus creates final proof package.

17. Developer sees:
   - final patch
   - tests run
   - files changed
   - contract pass/fail
   - review notes
```

---

## 14. Agent Loop Design

The MVP `chorus-lite` agent should be simple.

```text
observe
→ decide one tool call
→ receive result
→ continue until final_patch or budget exhausted
```

Pseudo-code:

```python
while not state.done:
    if budget.exceeded():
        fail("budget_exceeded")

    observation = build_observation(state, contract, recent_events)

    action = agent.next_action(observation)

    if action.type == "final_patch":
        break

    decision = policy_engine.evaluate(action, context)

    if decision.deny:
        event_store.append(tool_denied(action, decision))
        state.add_observation(decision.reason)
        continue

    result = tool_executor.execute(action)
    event_store.append(tool_result(action, result))

    state.update(result)

    if should_verify(state):
        verification = verifier.run(contract)
        state.update(verification)
```

---

## 15. Tool Proxy

The model should never touch the filesystem directly.

It only asks Chorus to perform typed actions:

```text
list_files(glob)
search(query, glob)
read_file(path)
apply_patch(patch)
run_test(command)
git_diff()
finish(summary)
```

Every tool call goes through:

```text
ToolRequest
→ PolicyEngine
→ ToolExecutor
→ EventStore
→ Observation
```

This is how Chorus becomes a real harness instead of just a wrapper.

---

## 16. Policy Engine

### 16.1 Policy Decision Types

```text
ALLOW
DENY
ASK_HUMAN
ESCALATE_MODEL
ESCALATE_WORKFLOW
```

### 16.2 File Edit Rule

```text
If path matches files.allow_edit → allow.
If path matches files.deny_edit → deny.
If path is unknown and risk is low → ask human.
If path is unknown and risk is high → deny.
```

### 16.3 Shell Rule

Allow:

```text
pytest
npm test
npm run typecheck
python -m compileall
```

Deny by default:

```text
rm -rf
curl
wget
git push
pip install unless dependency-change contract
npm install unless dependency-change contract
```

### 16.4 Diff Rule

```text
If changed files > max_files_changed → fail contract.
If diff lines > max_diff_lines → fail or ask human.
If forbidden file touched → block.
```

### 16.5 Budget Rule

```text
If cost > max_cost_usd → stop.
If tool calls > max_tool_calls → stop.
If two failed attempts → maybe escalate model.
```

---

## 17. Contract Compiler

The contract compiler turns messy tasks into executable constraints.

### 17.1 Inputs

```text
issue text
failing test command
repo tree
traceback
git diff
project config
```

### 17.2 Outputs

```text
Contract YAML
```

### 17.3 Compiler Steps

```text
1. Detect task type.
2. Detect language/framework.
3. Detect likely files.
4. Infer safe read/edit boundaries.
5. Infer required tests.
6. Infer risk level.
7. Infer budget.
8. Produce contract.
```

### 17.4 Risk Levels

Low risk:

```text
docs
comments
simple test update
small frontend copy change
```

Medium risk:

```text
business logic
backend bug
checkout calculation
parser behavior
```

High risk:

```text
auth
payment
migrations
security
dependency upgrade
infra
```

---

## 18. Verification System

Verification should not only mean “tests passed.”

It should check:

```text
target test
related tests
static checks
diff size
forbidden files
dependency changes
secret exposure
contract satisfaction
```

Example output:

```json
{
  "target_test": "passed",
  "related_tests": "passed",
  "forbidden_files": [],
  "changed_files": [
    "src/checkout/calculate_total.py",
    "tests/checkout/test_total.py"
  ],
  "diff_lines": 84,
  "contract_passed": true
}
```

---

## 19. PR Proof Package

The final report should be easy for a human reviewer to trust.

Example:

```markdown
# Chorus PR Proof

## Task
Fix failing checkout total calculation test.

## Verdict
PASS

## Contract
- Risk: Medium
- Max budget: $0.50
- Max files changed: 3
- Forbidden paths: auth, payments, migrations

## Evidence Before Fix
- Command: pytest tests/checkout/test_total.py -q
- Result: Failed
- Failure reproduced: Yes

## Evidence After Fix
- Target test: Passed
- Related tests: Passed
- Static checks: Passed
- Forbidden files touched: No
- Dependency changes: No

## Changed Files
- src/checkout/calculate_total.py
- tests/checkout/test_total.py

## Budget
- Model calls: 4
- Tool calls: 19
- Estimated cost: $0.08

## Review Focus
Please verify discount rounding behavior in calculate_total.py.

## Final Diff
...
```

---

## 20. CLI Design

### 20.1 `murmur init`

```bash
murmur init
```

Creates:

```text
.murmur/
  config.yaml
  policies/
    default.yaml
  contracts/
  runs/
```

---

### 20.2 `murmur fix-test`

```bash
murmur fix-test --cmd "pytest tests/foo/test_bar.py -q"
```

Runs the contract-first fix loop.

---

### 20.3 `murmur contract create`

```bash
murmur contract create --from-test "pytest tests/foo/test_bar.py -q"
```

Only generates the contract. Does not run the agent.

---

### 20.4 `murmur contract check`

```bash
murmur contract check .murmur/contracts/task.yaml
```

Validates the contract.

---

### 20.5 `murmur run`

```bash
murmur run .murmur/contracts/task.yaml
```

Executes the contract.

---

### 20.6 `murmur report open`

```bash
murmur report open
```

Opens the HTML report.

---

### 20.7 `murmur verify-pr`

```bash
murmur verify-pr --base main --head agent/fix
```

Verifies an existing AI-generated PR/diff against a generated or manual contract.

---

## 21. UI / Report Design

Do not overbuild the UI first.

Keep HTML reporting, but make it focused.

Important pages:

```text
1. Run Overview
2. Contract View
3. Execution Timeline
4. Tool Calls
5. Policy Decisions
6. Test Results
7. Diff Risk
8. PR Proof
```

Most important UI panel:

```text
CONTRACT STATUS

✅ Failure reproduced
✅ Target test passed
✅ Related tests passed
✅ No forbidden files touched
✅ Budget respected
⚠️ Medium risk path touched: src/checkout
```

The UI should feel like an engineering console, not a chatbot.

---

## 22. MVP Build Plan

### Phase 0: Freeze Old Direction

Stop adding more SWE-bench reporting features.

Keep:

```text
event log
HTML report
SWE-bench evaluator
pass/fail metrics
```

Do not prioritize:

```text
more leaderboards
more dashboards
expensive external agents
SWE-bench score chasing
```

---

### Phase 1: Domain Model

Build:

```text
domain/contract.py
domain/event.py
domain/policy.py
domain/tool.py
domain/run.py
domain/verification.py
```

Add tests for:

```text
contract parsing
contract validation
glob allow/deny behavior
budget checks
event serialization
```

---

### Phase 2: Local Worktree Sandbox

Build:

```text
adapters/sandboxes/local_worktree.py
```

Responsibilities:

```text
create temporary git worktree
copy repo state
run commands with timeout
capture stdout/stderr
cleanup or preserve run directory
```

Use local worktree before Docker.

Docker can come later.

---

### Phase 3: Tool Proxy

Build tools:

```text
list_files
search
read_file
apply_patch
run_test
git_diff
```

Every tool must emit events.

No model required yet.

Test manually with scripted commands first.

---

### Phase 4: Policy Engine

Build:

```text
policy/engine.py
policy/rules/file_access.py
policy/rules/budget.py
policy/rules/shell_command.py
policy/rules/diff_size.py
```

Test policy engine directly before adding the agent.

---

### Phase 5: Contract Compiler for Failing Tests

Build:

```text
application/contract_compiler.py
application/workflows/fix_test.py
```

Minimum logic:

```text
run failing command
parse traceback
extract likely paths
generate allowed read/edit paths
generate related test command
generate budget
```

---

### Phase 6: Chorus Lite Agent

Build:

```text
adapters/agents/chorus_lite.py
```

It should support structured output:

```json
{
  "thought_summary": "I need to inspect the failing checkout function.",
  "action": {
    "type": "read_file",
    "path": "src/checkout/calculate_total.py"
  }
}
```

Important:

```text
Do not let the agent output random free-form text.
Actions must be typed.
Invalid actions should be rejected and logged.
```

---

### Phase 7: Verification and Proof

Build:

```text
application/verifier.py
application/proof_builder.py
adapters/reporters/markdown.py
adapters/reporters/html.py
```

Output files:

```text
.murmur/runs/<run_id>/events.jsonl
.murmur/runs/<run_id>/contract.yaml
.murmur/runs/<run_id>/diff.patch
.murmur/runs/<run_id>/proof.md
.murmur/runs/<run_id>/report.html
```

---

### Phase 8: `verify-pr`

After `fix-test`, build:

```bash
murmur verify-pr --base main --head agent-branch
```

This is valuable because it works even when the code was generated by Cursor, Claude Code, Copilot, OpenHands, or a human.

It turns Chorus into a layer around existing tools.

---

## 23. What to Delete or De-Prioritize

De-prioritize:

```text
SWE-bench as primary product
generic benchmark comparison
generic agent observability
generic model router
generic security dashboard
```

Keep SWE-bench as:

```text
credibility benchmark later
integration test for agent adapters
demo mode
```

---

## 24. Competitive Differentiation

### 24.1 Cursor / Claude Code / Copilot

They are coding agents or coding environments.

Chorus should be the contract layer around the change.

```text
They answer:
Can the agent implement this?

Chorus answers:
Was the implementation bounded, verified, and reviewable?
```

---

### 24.2 LangSmith / Braintrust / Langfuse

They are observability/evaluation platforms.

Chorus should be software-change specific.

```text
They trace LLM apps.

Chorus verifies AI-generated code changes against contracts.
```

---

### 24.3 Invariant / Portkey / IronCurtain / Maybe Don’t

They focus on tool/action governance and agent security.

Chorus should be more specific.

```text
They secure agent actions.

Chorus verifies software changes.
```

The wedge:

> **PR trust layer for AI coding agents.**

---

## 25. Metrics to Track

### 25.1 Execution Metrics

```text
task_type
risk_level
runtime_seconds
model_calls
tool_calls
cost_usd
patch_attempts
test_attempts
```

### 25.2 Contract Metrics

```text
contract_passed
failure_reproduced
target_test_passed
related_tests_passed
forbidden_files_touched
diff_lines
files_changed
budget_exceeded
```

### 25.3 Reliability Metrics

```text
pass@1
pass^k
same_file_consensus
patch_variance
failure_taxonomy
cost_per_success
```

### 25.4 Review Metrics Later

```text
PR accepted
PR changed by human
PR rejected
review comments
reverted later
```

---

## 26. Failure Taxonomy

Use specific failure types:

```text
failure_not_reproduced
empty_patch
wrong_file
forbidden_file_requested
forbidden_file_touched
budget_exceeded
timeout
test_still_failing
related_test_regression
syntax_error
dependency_change_blocked
diff_too_large
model_invalid_action
tool_error
policy_denied
human_approval_required
```

Make `empty_patch` a first-class category because it already appeared in the earlier murmur run.

---

## 27. First Demo Scenario

Use a tiny local repo.

Create a bug:

```python
def apply_discount(price, discount):
    return price - discount
```

Failing test:

```python
def test_discount_percentage():
    assert apply_discount(100, 0.2) == 80
```

Run:

```bash
murmur fix-test --cmd "pytest tests/test_checkout.py -q" --budget 0.20
```

Expected proof:

```text
Failure reproduced: yes
Patch applied: yes
Target test passed: yes
Related tests passed: yes
Files changed: 1
Forbidden files touched: no
Budget used: $0.03
Verdict: PASS
```

This proves the product without SWE-bench cost.

---

## 28. Final Architecture Identity

Chorus should be described as:

> Chorus is a contract-first execution harness. Its core is a deterministic run orchestrator that compiles a coding task into an enforceable contract, executes an AI agent through a policy-controlled tool proxy inside an isolated worktree, verifies the resulting diff against tests and software-change rules, records an append-only event log, and produces a PR proof package for human review.

---

## 29. Immediate Implementation Checklist

Build in this order:

```text
1. Contract schema
2. Event schema
3. Local worktree sandbox
4. Tool proxy
5. Policy engine
6. fix-test workflow
7. Chorus Lite agent adapter
8. Verifier
9. PR proof markdown
10. HTML report
11. verify-pr command
12. External agent adapters
```

The most important first file:

```text
murmur/domain/contract.py
```

The most important first command:

```text
murmur fix-test
```

The most important first product output:

```text
PR proof package
```

---

## 30. Summary

The strongest Chorus pivot is not:

```text
another coding agent
another benchmark runner
another model router
another security dashboard
```

The strongest Chorus pivot is:

> **A contract-first software-change harness for AI coding agents.**

Its job is to make AI-generated code changes:

```text
bounded
safe
testable
reviewable
cost-controlled
reproducible
```

The unique idea is not that Chorus can write code.

The unique idea is that Chorus forces AI-written code to satisfy an engineering contract before humans trust the PR.
