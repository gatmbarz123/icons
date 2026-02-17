#!/usr/bin/env python3
"""
EC2 Windows Instance Manager — FastAPI Backend
Provides REST API endpoints to start/stop EC2 instances with scheduler-override tags.
Uses local AWS credentials (same as running `aws ec2` from your terminal).
"""

import os
import traceback
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel
import boto3
import uvicorn

# ─── Configuration ──────────────────────────────────────────────────────────────

AWS_REGION = os.getenv("AWS_REGION", "eu-central-1")

# Whitelisted instance IDs — only these can be managed
ALLOWED_INSTANCES = {
    "i-02d6e1b688f2184ec": {"name": "Test-vpn", "country": "il"},
}

# ─── App Setup ──────────────────────────────────────────────────────────────────

app = FastAPI(title="EC2 Instance Manager", version="1.0.0")

# No CORS middleware needed — frontend is served from the same origin

ec2 = boto3.client("ec2", region_name=AWS_REGION)

# ─── Models ─────────────────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    hours: int = 3

# ─── Helpers ────────────────────────────────────────────────────────────────────

def validate_instance(instance_id: str):
    if instance_id not in ALLOWED_INSTANCES:
        raise HTTPException(status_code=403, detail=f"Instance {instance_id} is not in the allowed list")

def get_instance_config(instance_id: str) -> dict:
    return ALLOWED_INSTANCES.get(instance_id, {"name": "Unknown", "country": "us"})

# ─── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/api/instances")
def list_instances():
    """List all managed instances with their current state."""
    results = []
    instance_ids = list(ALLOWED_INSTANCES.keys())
    
    # Filter out dummy example IDs if they don't exist in AWS (to prevent crashes) creates partial success
    # But for now let's just try to fetch all, and catch 404s for individual ones if needed? 
    # aws ec2 describe-instances fails if ANY id is not found? No, it fails if ID format is invalid.
    # Non-existent IDs usually return "InvalidInstanceID.NotFound" if they look like IDs.
    
    # We'll valid IDs only for the actual AWS call
    real_ids = [k for k in instance_ids if k.startswith("i-") and "example" not in k]
    dummy_ids = [k for k in instance_ids if "example" in k]

    try:
        if real_ids:
            resp = ec2.describe_instances(InstanceIds=real_ids)
            for reservation in resp["Reservations"]:
                for instance in reservation["Instances"]:
                    iid = instance["InstanceId"]
                    config = get_instance_config(iid)
                    tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
                    
                    results.append({
                        "id": iid,
                        "name": tags.get("Name", config["name"]),
                        "country": config["country"],
                        "state": instance["State"]["Name"],
                        "override": tags.get("scheduler-override", None),
                    })
    except Exception as e:
        traceback.print_exc()
        print(f"AWS Error: {e}")
        # Don't expose raw AWS error details to clients
        # If AWS fails (e.g., no credentials locally), show instances in simulated state
        for iid in real_ids:
            config = get_instance_config(iid)
            results.append({
                "id": iid,
                "name": config["name"],
                "country": config["country"],
                "state": "stopped",  # Simulated - no AWS access
                "override": None,
            })

    # Add dummy instances for UI demonstration (simulated state)
    for iid in dummy_ids:
        config = get_instance_config(iid)
        results.append({
            "id": iid,
            "name": config["name"],
            "country": config["country"],
            "state": "stopped", # Simulated
            "override": None,
        })
    
    # Sort by the order in ALLOWED_INSTANCES
    order = list(ALLOWED_INSTANCES.keys())
    results.sort(key=lambda x: order.index(x["id"]) if x["id"] in order else 999)
    
    return {"instances": results}


@app.post("/api/instances/{instance_id}/start")
def start_instance(instance_id: str, req: StartRequest):
    """Start an instance and set a scheduler-override tag."""
    validate_instance(instance_id)
    
    if req.hours < 1 or req.hours > 8:
        raise HTTPException(status_code=400, detail="Override hours must be between 1 and 8")
    
    try:
        # Start the instance
        ec2.start_instances(InstanceIds=[instance_id])
        
        # Calculate override expiry
        override_until = datetime.now(timezone.utc) + timedelta(hours=req.hours)
        override_value = override_until.strftime("%Y-%m-%dT%H:%M")
        
        # Set the override tag
        ec2.create_tags(
            Resources=[instance_id],
            Tags=[{"Key": "scheduler-override", "Value": override_value}]
        )
        
        return {
            "status": "success",
            "message": f"Started {instance_id} with {req.hours}h override (until {override_value} UTC)",
            "instance_id": instance_id,
            "override_until": override_value,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to start instance. Check server logs.")


@app.post("/api/instances/{instance_id}/stop")
def stop_instance(instance_id: str):
    """Stop an instance and remove the scheduler-override tag."""
    validate_instance(instance_id)
    
    try:
        # Stop the instance
        ec2.stop_instances(InstanceIds=[instance_id])
        
        # Remove the override tag
        ec2.delete_tags(
            Resources=[instance_id],
            Tags=[{"Key": "scheduler-override"}]
        )
        
        return {
            "status": "success",
            "message": f"Stopped {instance_id} and removed override tag",
            "instance_id": instance_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to stop instance. Check server logs.")


# ─── Serve Static Files ────────────────────────────────────────────────────────
# Serve the website files from the same directory

@app.get("/")
def serve_index():
    return FileResponse("index.html")

@app.get("/ec2")
def serve_ec2():
    with open("ec2.html", "r", encoding="utf-8") as f:
        content = f.read()
    response = HTMLResponse(content=content)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# Redirect .html paths to clean URLs
@app.get("/ec2.html")
def redirect_ec2_html():
    return RedirectResponse(url="/ec2")

@app.get("/index.html")
def redirect_index_html():
    return RedirectResponse(url="/")

# Mount only icons directory — don't expose source files
app.mount("/icons", StaticFiles(directory="icons"), name="icons")

# ─── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    host = os.getenv("HOST", "127.0.0.1")
    print(f"🚀 EC2 Instance Manager running at http://{host}:{port}")
    print(f"📄 Dashboard:  http://{host}:{port}/")
    print(f"🖥️  EC2 Manager: http://{host}:{port}/ec2")
    uvicorn.run(app, host=host, port=port)

