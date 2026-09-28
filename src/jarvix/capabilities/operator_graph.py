"""Stable dependency planning and bounded read-only concurrency policy."""
import copy

PARALLEL_READS = frozenset({"tasks.list", "notes.search", "memory.search", "projects.list", "apps.list",
                            "system.status", "system.processes", "files.inspect", "files.list",
                            "files.folder_summary", "files.preview"})


def references(value):
    if isinstance(value, dict):
        if "$ref" in value and isinstance(value["$ref"], str):
            return {value["$ref"].split(".")[0]}
        return set().union(*(references(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(references(item) for item in value))
    return set()


def ordered(plan, seed=()):
    """Missing depends_on preserves 0.3 order; [] explicitly permits independence."""
    value = copy.deepcopy(plan)
    names = [step["id"] for step in value["steps"]]
    if len(set(names)) != len(names):
        raise ValueError("Step IDs must be unique.")
    known, done, previous = set(names) | set(seed), set(seed), None
    pending = value["steps"]
    for step in pending:
        deps = set(step.get("depends_on", [previous] if previous else []))
        deps |= references(step["arguments"])
        deps |= references(step.get("expected", {}).get("arguments", {})) - {step["id"]}
        if step["id"] in deps or not deps <= known:
            raise ValueError("Unknown or self-referencing step dependency.")
        step["depends_on"] = sorted(deps)
        previous = step["id"]
    result = []
    while pending:
        ready = next((step for step in pending if set(step["depends_on"]) <= done), None)
        if ready is None:
            raise ValueError("Step dependencies contain a cycle.")
        result.append(ready)
        pending.remove(ready)
        done.add(ready["id"])
    value["steps"] = result
    return value


def batches(plan, registry, seed=()):
    pending, done = list(plan["steps"]), set(seed)
    while pending:
        ready = [step for step in pending if set(step["depends_on"]) <= done]
        if not ready:
            raise ValueError("A required dependency did not complete.")
        batch = [ready[0]]
        def safe(step):
            return (step["tool"] in PARALLEL_READS and not step.get("expected")
                    and registry.get(step["tool"]).permission_level == 1
                    and registry.get(step["tool"]).risk == "read")
        if safe(ready[0]) and plan.get("max_parallel_reads", 3) > 1:
            for step in ready[1:]:
                if not safe(step):
                    break  # Mutations are explicit barriers, including independent ones.
                batch.append(step)
                if len(batch) >= plan.get("max_parallel_reads", 3):
                    break
        yield batch
        for step in batch:
            pending.remove(step)
            done.add(step["id"])

