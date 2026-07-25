"""Core package — config, LLM factory, graph state, pipeline, tracing.

Keep this module free of eager imports that pull in ``agents`` (via
``core.pipeline``) to avoid circular imports when agent nodes load
``core.graph_state``.
"""
