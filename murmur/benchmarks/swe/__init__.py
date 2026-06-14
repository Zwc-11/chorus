"""SWE-bench evaluation harness: turn the loaded task specs into a real ``pass^k``.

This package is the missing half of Phase 5 item 7 -- the code that produces the
headline number. The loader (:mod:`murmur.benchmarks.swebench`) gives the *tasks*;
this package runs an agent against them N times under two scaffolds, evaluates the
patches with the official SWE-bench test harness, and folds the resolved/not
outcomes into the same ``SuiteResult`` the regression gate already consumes.

The design holds the model fixed and varies **only the scaffold** -- the whole
credibility of "changing only the harness moved pass^k" depends on that. The two
built-in scaffolds (:mod:`.scaffold`) differ in exactly one dimension: a
self-repair turn.

Nothing here calls a model or Docker at import time. Real patch models
(:class:`.model.AnthropicPatchModel`, :class:`.model.DeepSeekPatchModel`, or
:func:`.providers.create_patch_model`) and the real evaluator
(:class:`.evaluator.SubprocessSweEvaluator`) import their heavy dependencies
lazily and raise a clear error if they are missing, so the package -- and its
tests, which inject fakes -- run offline at zero cost. Producing the actual number
needs ``DEEPSEEK_API_KEY`` or ``ANTHROPIC_API_KEY`` + Docker +
``pip install 'murmur-ai-harness[bench]'``.
"""
