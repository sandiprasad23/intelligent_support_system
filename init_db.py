from src.logging_store import _database_path_from_config, initialize_database


if __name__ == "__main__":
    initialize_database(_database_path_from_config())
    print("Database initialized successfully.")