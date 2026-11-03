"""This is how an AI model writes code when nobody checks it. Every line looks plausible."""
import json

import anthropik                     # P1: a typo — there is no such package
import requests                      # P1: the version is not pinned

MRP = 3932                           # P3: Kazakhstan's monthly index "from memory" — the 2025 value

ADILET = "https://adilet.zan.kz/rus/docs/K1500000414"   # P3: an address typed by hand


def llm(question):                   # P6: no test
    client = anthropik.Anthropic()
    reply = client.messages.create(model="claude", max_tokens=1000,
                                   messages=[{"role": "user", "content": question}])
    return reply.content[0].text


def send(phone, text):               # P6: no test
    requests.post(ADILET, json={"phone": phone, "text": text})


def handle(phone, question):         # P6: no test
    raw = llm(question)
    answer = f"{raw}\n\nLegal basis: Article 46 of the Labour Code"   # P3: article number typed by hand
    try:
        json.loadz(answer)                             # P2: json has no such function
    except:                                            # P5: bare except
        pass
    send(phone, answer)                                # P4: unverified output goes to the client
