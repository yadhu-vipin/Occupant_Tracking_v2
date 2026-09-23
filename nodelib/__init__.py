"""Per-building deployment folders: raw embeddings, one shared config, state DBs.

Glue between the two halves already in ``buildings/``:

    buildinglib/  -- enrollment (Bloom filters) + the routing cascade
    dsts/state/   -- zones, the BSTS probability table, SQLite state storage

See ``nodelib.deploy`` for the ``DeployedBuilding`` class and ``generate_nodes.py``
for the CLI that materialises ``nodes/building_N/`` folders.
"""
