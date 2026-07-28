from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint.

    :return: Status response.
    """
    return {"status": "ok"}
