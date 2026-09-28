import numpy as np

def foo(x):
    return bar(x) + 1

def bar(x):
    y = np.zeros((300,300), dtype=np.float64)
    for i in range(2000):
        y = y + 1.0001
    return float(y.sum())

for _ in range(200):
    foo(1)
