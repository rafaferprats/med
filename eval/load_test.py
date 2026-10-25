"""Several people asking at the same time: does the model answer them together or in a queue?

Sends N questions at once to the running server and measures, for each person, when the
first words appear and when the answer is complete. Creates throw-away test accounts in the
database and deletes them at the end.

    ./venv/bin/python eval/load_test.py            # 3 people
    ./venv/bin/python eval/load_test.py 5          # 5 people
    ./venv/bin/python eval/load_test.py 3 dudosas  # questions that go through the topic classifier
"""
import asyncio
import sqlite3
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import auth  # noqa: E402
from config import DB_PATH  # noqa: E402

BASE = "http://localhost:8000"
QUESTIONS = [
    "Me duele la garganta desde ayer y tengo algo de fiebre, ¿qué hago?",
    "Tengo acidez después de cenar casi todos los días, ¿qué puedo tomar?",
    "Me he torcido el tobillo bajando una escalera y se ha hinchado, ¿qué hago?",
    "Llevo una semana durmiendo fatal y me despierto cansado, ¿qué me recomiendas?",
    "Tengo alergia primaveral y me pican mucho los ojos, ¿qué tomo?",
]
# Not clearly health or not by the rules: the model classifies them first (main.on_topic)
UNSURE = [
    "¿Es bueno el café por las mañanas?",
    "Me torcí el tobillo jugando al fútbol, ¿qué hago?",
    "¿Es malo comer mucho azúcar?",
    "¿Es bueno caminar descalzo por casa?",
    "¿Cuánta agua hay que beber al día?",
]


async def one_person(n: int, question: str, start: asyncio.Event) -> dict:
    email = f"carga-{n}-{int(time.time())}@example.com"
    auth.create_user(email, "carga-prueba-123")
    with sqlite3.connect(DB_PATH) as db:  # Premium, so the free limit doesn't get in the way
        db.execute("UPDATE users SET plan='premium', premium_until='2099-01-01T00:00:00+00:00' WHERE email=?", (email,))
    async with httpx.AsyncClient(base_url=BASE, timeout=300) as c:
        await c.post("/auth/login", json={"email": email, "password": "carga-prueba-123"})
        await start.wait()  # everybody sends at the same moment
        t0 = time.perf_counter()
        first = None
        text = ""
        async with c.stream("POST", "/chat/stream", json={"message": question}) as r:
            async for chunk in r.aiter_text():
                if chunk.strip() and first is None:
                    first = time.perf_counter() - t0
                text += chunk
        return {"n": n, "question": question, "first": first, "total": time.perf_counter() - t0,
                "words": len(text.split()), "status": r.status_code}


async def main(people: int, questions: list[str]) -> None:
    start = asyncio.Event()
    tasks = [asyncio.create_task(one_person(i + 1, questions[i % len(questions)], start)) for i in range(people)]
    await asyncio.sleep(1.5)  # accounts created and logged in
    print(f"{people} personas preguntan a la vez...\n")
    start.set()
    results = await asyncio.gather(*tasks)
    for r in sorted(results, key=lambda r: r["total"]):
        print(f"Persona {r['n']}: primeras palabras a los {r['first']:5.1f} s · respuesta completa a los "
              f"{r['total']:5.1f} s · {r['words']} palabras · «{r['question'][:45]}…»")
    firsts = sorted(r["first"] for r in results)
    print(f"\nEl primero ve texto a los {firsts[0]:.1f} s; el último, a los {firsts[-1]:.1f} s.")
    with sqlite3.connect(DB_PATH) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("DELETE FROM users WHERE email LIKE 'carga-%@example.com'")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3,
                     UNSURE if "dudosas" in sys.argv[2:] else QUESTIONS))
