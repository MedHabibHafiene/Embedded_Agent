from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uvicorn
import os
import uuid
import subprocess
from typing import Dict, Any
from stm32_agent import orchestration_pipeline, init_ollama_client, Stm32RagStore

app = FastAPI(title="STM32 Agent API")

# In-memory store for generated firmware jobs
jobs: Dict[str, Any] = {}

class GenerateRequest(BaseModel):
    prompt: str

class CommandRequest(BaseModel):
    command: str
    auto_confirm: bool = True

class FlashRequest(BaseModel):
    id: str
    confirmed: bool

@app.post("/api/generate")
async def generate(request: GenerateRequest):
    try:
        # Initialize clients for the pipeline
        client = init_ollama_client()
        rag_store = Stm32RagStore()
        
        # Generate code and hardware state
        # Note: orchestration_pipeline now returns the structured result
        result = orchestration_pipeline(
            command=request.prompt,
            rag_store=rag_store,
            client=client,
            auto_confirm=False # Do not flash during generation
        )
        
        job_id = str(uuid.uuid4())
        jobs[job_id] = {
            "code": result["c_code"],
            "hardware_state": result["hardware_state"],
            "command": request.prompt,
            "status": "generated"
        }
        
        return {
            "id": job_id,
            "code": result["c_code"],
            "hardware_state": result["hardware_state"]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate-and-flash")
async def generate_and_flash(request: CommandRequest):
    try:
        # The orchestration_pipeline handles RAG, LLM, Build, and Flash
        orchestration_pipeline(
            command=request.command,
            auto_confirm=request.auto_confirm
        )
        return {"status": "success", "message": f"Successfully processed command: {request.command}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/flash")
async def flash(request: FlashRequest):
    if request.id not in jobs:
        raise HTTPException(status_code=404, detail="Job ID not found")
    
    if not request.confirmed:
        raise HTTPException(status_code=400, detail="Flash not confirmed")
    
    job = jobs[request.id]
    
    try:
        # Write the stored code to the project
        from stm32_agent import archive_and_remove_source, write_code_to_platformio
        from pathlib import Path
        write_code_to_platformio(Path("./stm32_project"), job["code"])
        
        # Run PlatformIO upload
        # Using subprocess.run for simplicity; in production use a task queue
        process = subprocess.run(
            ["pio", "run", "--target", "upload"],
            cwd="./stm32_project",
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if process.returncode != 0:
            return {
                "status": "error",
                "logs": process.stdout + "\n" + process.stderr
            }

        archive_and_remove_source(Path("./stm32_project"), job.get("command", ""))
            
        return {
            "status": "success",
            "logs": process.stdout
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
