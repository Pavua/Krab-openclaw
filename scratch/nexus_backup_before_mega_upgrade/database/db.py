from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import os
import time

Base = declarative_base()

class Interaction(Base):
    __tablename__ = 'interactions'
    
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    user_id = Column(String(50))
    command = Column(String(50))
    query = Column(Text)
    response = Column(Text)

class Database:
    def __init__(self):
        # Default to local sqlite if no POSTGRES env (for backward compatibility/testing)
        pg_user = os.getenv("POSTGRES_USER", "nexus")
        pg_pass = os.getenv("POSTGRES_PASSWORD", "nexus_password")
        pg_db = os.getenv("POSTGRES_DB", "nexus_data")
        pg_host = os.getenv("POSTGRES_HOST", "db") # 'db' is the docker service name
        
        # Check if running in Docker (or if we want to force PG)
        if os.getenv("DOCKER_CONTAINER"):
            self.db_url = f"postgresql://{pg_user}:{pg_pass}@{pg_host}/{pg_db}"
        else:
            self.db_url = "sqlite:///nexus.db"
            
        self.engine = create_engine(self.db_url)
        self.Session = sessionmaker(bind=self.engine)
        
    def init_db(self):
        # Wait for DB to be ready if in docker
        if "postgresql" in self.db_url:
            max_retries = 5
            for i in range(max_retries):
                try:
                    Base.metadata.create_all(self.engine)
                    print("Database connected and initialized.")
                    return
                except Exception as e:
                    print(f"Waiting for DB... ({e})")
                    time.sleep(2)
        else:        
            Base.metadata.create_all(self.engine)

    def log_interaction(self, user_id, command, query, response):
        session = self.Session()
        try:
            interaction = Interaction(
                user_id=str(user_id),
                command=command,
                query=query,
                response=response
            )
            session.add(interaction)
            session.commit()
        except Exception as e:
            print(f"Failed to log interaction: {e}")
        finally:
            session.close()
