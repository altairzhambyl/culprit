# Agents for Humans: Building an AI Agent That Debugs ML Regressions with Experiments, Not Guesses

An ML system can keep training successfully while its quality deteriorates. Unit tests cover software behavior, but often miss statistical consequences. A harmless-looking preprocessing refactor can change what the same feature means between training and evaluation. Monitoring finds the drop. An engineer still has to investigate it.

## The repetitive job
An investigation often starts with the last good nightly evaluation and a handful of merged commits. The engineer checks out candidates, retrains or evaluates each, compares metrics, reads the responsible diff, implements a repair, and verifies the outcome. Culprit packages this workflow as an autonomous agent that asks the engineer for the consequential approval.

## Why experiments come before explanations
A language model can read a diff and offer a plausible story. Plausibility is not causal evidence. Culprit gives its Strands agent a `run_experiment` tool that checks out a commit into a detached Git worktree and runs the target project's configured command. It returns the measured metric and errors. The agent calibrates a known-good and known-bad commit under the same fast configuration, then narrows the window by bisection. The culprit and its parent must both have been measured for the evaluator to call the evidence sufficient.

## Where Strands matters
One Strands Agent coordinates twelve tools. Sequential execution prevents simultaneous operations from fighting over a worktree. Hooks enforce budgets, detect repeated calls and append an inspectable trace. A BeforeToolCall hook interrupts the agent before a pull request is opened. FileSessionManager persists the conversation so the dashboard or CLI can resume it later with the human's decision. Pydantic defines the structured incident report.

The background workflow records a nightly evaluation, detects a threshold-crossing regression, and starts one investigation per good-to-bad window. This is how the agent saves attention: the user does not need to start each investigation by hand.

## An evaluator that checks the work
The evaluator receives scenario ground truth separately from the agent's input. After the investigation ends, it independently evaluates the fixed branch and reruns the project's tests. It also checks the predicted culprit, the measured bracket and the addition of a regression-test file. It does not accept a prose claim of recovery as proof.

## Results actually obtained
The recorded churn integration run used the scripted provider, not a live LLM. That policy knows the churn repair. Its tools nevertheless ran real training and evaluations: nightly F1 fell from 0.8301 to 0.6624; five experiments isolated the culprit. The independent evaluator measured fast-config F1 of 0.7027 at the bad boundary and 0.8111 on the repaired branch, equal to the fast good baseline. The project tests passed, a guard-test file was added, and a human approval resumed the recorded run before the local PR artifact was delivered.

Release verification passed 67 tests on Windows. Review also exposed issues with Windows shell quoting, read-only Git objects, atomic state replacement during dashboard reads, and a process-wide auto-approve flag leaking across API requests. Approval mode is now saved per run, preserving the explicit human decision for subsequent investigations.

## AWS and deployment status
Amazon Bedrock is the default real-model provider in the code, and an Amazon Bedrock AgentCore entrypoint is included. Neither a live Bedrock investigation nor an AgentCore cloud deployment was completed: provider credentials were unavailable in the review environment. The demonstrated runtime is local. There are no cloud invocation results to report.

The fraud-risk scenario is the next evaluation target for a real model. It is intentionally separate from the churn-specific scripted repair. Until that real run succeeds, the offline result demonstrates integration correctness, not unseen-regression generalization. This distinction matters more than a deployment badge.

## Reproduce and inspect
Source, setup instructions, both generated scenarios, tests, architecture and the recorded-run evidence are available at https://github.com/danialmukash-cell/culprit. Start with the scripted provider for offline reproduction, then configure an authorized provider, run `culprit doctor`, and evaluate the fraud scenario.

Culprit's principle is simple: use model reasoning to choose the next experiment, use measurements to judge the result, and leave the consequential decision with the engineer.

---
Publication-ready draft. Not yet published on builder.aws.com. Replace the validation-status section only after a real provider run or cloud invocation has actually been verified.
