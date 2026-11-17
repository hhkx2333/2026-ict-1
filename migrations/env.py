from alembic import context
from c_app.db import Base, make_engine

with make_engine().connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()
