import asyncio
import time

def perform_heavy_calculation():
    time.sleep(3)  # 3초 동안 Event Loop 차단

async def task_a():
    while True:
        print("A: 계산 시작")
        perform_heavy_calculation()
        print("A: 계산 끝")
        await asyncio.sleep(0)

async def task_b():
    while True:
        print("B: 실행!")
        await asyncio.sleep(0.5)

async def main():
    await asyncio.gather(task_a(), task_b())

asyncio.run(main())

"""
A: 계산 시작
A: 계산 끝
B: 실행!
A: 계산 시작
A: 계산 끝
A: 계산 시작
A: 계산 끝
A: 계산 시작
A: 계산 끝
B: 실행!
A: 계산 시작
A: 계산 끝
A: 계산 시작
A: 계산 끝
A: 계산 시작
A: 계산 끝
B: 실행!
A: 계산 시작
^C^CTraceback (most recent call last):
"""