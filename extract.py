import json

log_path = r'C:\Users\Debanshu\.gemini\antigravity-ide\brain\8c6b6feb-24c8-44ed-9687-47d64b6d056b\.system_generated\logs\transcript.jsonl'
with open(log_path, 'r', encoding='utf-8') as f:
    for line in f:
        try:
            entry = json.loads(line)
            if 'tool_calls' not in entry and entry.get('source') == 'SYSTEM' and 'output' in entry.get('content', {}):
                content_str = str(entry['content']['output'])
                if 'class FlightManager' in content_str and '_smart_rtl_loop' in content_str:
                    print("Found _smart_rtl_loop in output!")
                    with open(r'd:\CityGrid\my-project\PhoneOS_Swarm\scratch_flight_manager_old.py', 'w', encoding='utf-8') as out:
                        out.write(content_str)
                    break
        except Exception as e:
            pass
