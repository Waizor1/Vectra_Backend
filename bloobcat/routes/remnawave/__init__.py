from .connect import router as connect_router
from .catcher import router as catcher_router

router = connect_router
router.include_router(catcher_router) 