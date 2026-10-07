"""
FastAPI backend for the STM32 agent.

Structure (HTTP concerns only - no business logic lives here):

    main.py       app factory, logging, lifespan singletons, error mapping
    schemas.py    request/response models (API contract)
    jobs.py       persistent job store (data/jobs/*.json)
    deps.py       dependency providers reading the app.state singletons
    routers/      one module per endpoint group
"""
