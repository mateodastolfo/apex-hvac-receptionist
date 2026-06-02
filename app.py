import os
import time
import requests
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions
import openai

app = Flask(__name__)

# 1. INITIALIZE EXTERNAL CORE APIS
openai.api_key = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
ZAPIER_NLA_API_KEY = os.getenv("ZAPIER_NLA_API_KEY")

SUPABASE_URL = os.getenv("SUPABASE_URL")
supabase: Client = create_client(
    SUPABASE_URL, 
    SUPABASE_KEY, 
    options=ClientOptions(postgrest_client_timeout=10, storage_client_timeout=10)
)


# 2. INBOUND TWILIO PHONE LINE WEBHOOK
@app.route("/voice-inbound", methods=['POST'])
def voice_inbound():
    """Triggered instantly when a San Antonio homeowner hits our Twilio line"""
    response = VoiceResponse()
    
    # Empathetic, localized greeting for high-volume cooling rushes
    gather = Gather(num_digits=1, action="/handle-response", method="POST", timeout=5)
    gather.say(
        "Thank you for calling San Antonio Emergency Cooling Services. All of our technicians are currently fixing units "
        "across the metroplex, but our automated system can secure your slot immediately. If your air conditioning is "
        "completely broken down, press 1. For routine maintenance or general tune ups, press 2.", 
        voice="Polly.Kendra", 
        language="en-US"
    )
    response.append(gather)
    response.redirect("/voice-inbound")
    return str(response)


# 3. CONTEXTUAL DIGIT PROCESSING
@app.route("/handle-response", methods=['POST'])
def handle_response():
    """Processes caller priority choice and prepares the speech recorder"""
    digit_pressed = request.form.get("Digits")
    response = VoiceResponse()
    
    if digit_pressed == "1":
        response.say("Understood. Marking this as an emergency system breakdown. Please state your name, home address, and system behavior after the tone.", voice="Polly.Kendra")
        response.record(max_length=40, action="/process-transcription")
    else:
        response.say("Thank you. Please state your name, phone number, and preferred afternoon for a technician to visit after the tone.", voice="Polly.Kendra")
        response.record(max_length=40, action="/process-transcription")
        
    return str(response)


# 4. AGENTIC EXECUTION & ROUTING ORCHESTRATOR
@app.route("/process-transcription", methods=['POST'])
def process_transcription():
    """Feeds voice context to the OpenAI Assistant and executes database logging tools"""
    recording_url = request.form.get("RecordingUrl")
    caller_phone = request.form.get("From")
    
    # Context payload for the active thread session
    user_voice_input = f"Inbound emergency call from {caller_phone}. Recording dispatch asset file: {recording_url}"

    # Step A: Initialize an isolated OpenAI Thread for this call session
    thread = openai.beta.threads.create()
    
    # Step B: Inject user context payload into the active thread
    openai.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=user_voice_input
    )
    
    # Step C: Trigger the execution run against your custom San Antonio Assistant
    run = openai.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=ASSISTANT_ID
    )
    
    # Step D: Monitor for functional execution callbacks requested by the AI
    while run.status in ["queued", "in_progress"]:
        time.sleep(1)
        run = openai.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        
        if run.status == "requires_action":
            tool_outputs = []
            for tool_call in run.required_action.submit_tool_outputs.tool_calls:
                func_name = tool_call.function.name
                args = openai.utils.json.loads(tool_call.function.arguments)
                
                # Dynamic Tool Router Execution
                if func_name == "trigger_zapier_action":
                    nla_url = "https://nla.zapier.com/api/v1/dynamic/exposed/"
                    headers = {"Authorization": f"Bearer {ZAPIER_NLA_API_KEY}", "Content-Type": "application/json"}
                    payload = {"instructions": f"Run {args.get('action_type')} with parameters: {args.get('appointment_details')}"}
                    res = requests.post(nla_url, json=payload, headers=headers)
                    tool_outputs.append({"tool_call_id": tool_call.id, "output": res.text})
                    
                elif func_name == "log_call_action":
                    log_data = {
                        "caller_phone": args.get("caller_phone", caller_phone),
                        "ai_summary": args.get("ai_summary", "HVAC Dispatch Ticket"),
                        "action_taken": args.get("action_taken", "Processed")
                    }
                    supabase.table("receptionist_logs").insert(log_data).execute()
                    tool_outputs.append({"tool_call_id": tool_call.id, "output": '{"status": "logged_successfully"}'})
            
            # Submit data back to OpenAI to finalize operations
            openai.beta.threads.runs.submit_tool_outputs(thread_id=thread.id, run_id=run.id, tool_outputs=tool_outputs)

    # Step E: Conclude call protocol safely with the user terminal
    response = VoiceResponse()
    response.say("Perfect, your dispatch file is finalized. Our automated scheduler is logging the slot now. Watch for a text confirmation within 60 seconds. Goodbye!", voice="Polly.Kendra")
    return str(response)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
