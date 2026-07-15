from dotenv import load_dotenv
load_dotenv()
from ai_handler import analyze_message
tests = [
    'recuerdame pagar el recibo del agua el 30 de julio a las 6pm',
    'recuerdame que el 30 de julio sale spiderman a las 8pm',
    'recuerdame pagarle 500 a juan el viernes',
]
for t in tests:
    r = analyze_message(t)
    print(f"{r.get('lead_minutes', '?')}min | {r.get('text', '')}")
