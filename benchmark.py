import timeit

# Current optimized version (set)
set_setup = """
BATTERY_SENSORS = {
    "batSoc", "pbat", "batSoh", "bat_charge_total", "bat_discharge_total",
    "bat_charging", "bat_discharging", "batV", "batI",
}
"""

set_code = """
"batV" in BATTERY_SENSORS
"not_there" in BATTERY_SENSORS
"""

# Unoptimized version (list)
list_setup = """
BATTERY_SENSORS = [
    "batSoc", "pbat", "batSoh", "bat_charge_total", "bat_discharge_total",
    "bat_charging", "bat_discharging", "batV", "batI",
]
"""

list_code = """
"batV" in BATTERY_SENSORS
"not_there" in BATTERY_SENSORS
"""

# Benchmark
n = 10_000_000
set_time = timeit.timeit(set_code, setup=set_setup, number=n)
list_time = timeit.timeit(list_code, setup=list_setup, number=n)

print(f"List membership check time: {list_time:.4f} seconds")
print(f"Set membership check time: {set_time:.4f} seconds")
print(f"Improvement: {(list_time - set_time) / list_time * 100:.2f}%")
