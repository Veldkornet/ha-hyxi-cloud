import timeit

setup = """
dtype = "HYBRID_INVERTER"
"""

code1 = """
any(x in dtype for x in ["BATTERY", "EMS", "HYBRID", "ALL_IN_ONE"])
"""

code2 = """
"BATTERY" in dtype or "EMS" in dtype or "HYBRID" in dtype or "ALL_IN_ONE" in dtype
"""

print("Generator:", timeit.timeit(code1, setup=setup, number=1000000))
print("Explicit: ", timeit.timeit(code2, setup=setup, number=1000000))
