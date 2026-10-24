"""Strategy impact analysis: individual, combined and pairwise evaluation of cooling strategies against one baseline.

Every scenario is run through the hourly simulation engine (app.simulation.engine.run_simulation); nothing is
obtained by adding up individual savings. Cost and space are estimated from quantity take-offs of the simulated
configuration, so shared infrastructure is counted once per scenario.
"""
