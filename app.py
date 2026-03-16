import os
from fastapi import FastAPI

app = FastAPI(title="PIVOT Engine", version="1.0.0")

@app.get("/health")
def health_check():
    return {
        "status": "healthy", 
        "service": "PIVOT", 
        "environment": "production"
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)