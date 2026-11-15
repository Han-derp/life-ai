from app.database.bootstrap import bootstrap_database
from app.database.connection import DB_PATH


def main() -> None:
    bootstrap_database()
    print("数据库初始化成功：")
    print(DB_PATH)


if __name__ == "__main__":
    main()
