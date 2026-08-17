'''
Author: lihuan
Date: 2026-08-13 20:12:38
LastEditors: lihuan
LastEditTime: 2026-08-14 00:36:43
Email: 17719495105@163.com
'''
"""测试连接远程 PostgreSQL 数据库。"""
import psycopg

# 连接信息
CONN_INFO = {
    "host": "127.0.0.1",  # 隧道 ssh -L 5432:127.0.0.1:5432 root@124.221.180.74
    "port": 5432,                     #本地端口  远端地址和端口
    "user": "admin",
    "password": "123456",
    "dbname": "default_db",
    # 远程连接建议设置超时,避免长时间挂起
    "connect_timeout": 10,
}


def test_connection() -> None:
    try:
        with psycopg.connect(**CONN_INFO) as conn:
            print("✅ 连接成功!")

            # 1. 服务端信息
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                version = cur.fetchone()[0]
                print(f"服务器版本: {version}")

            # 2. 当前数据库
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_user, inet_server_addr();")
                db, user, addr = cur.fetchone()
                print(f"数据库: {db} | 用户: {user} | 服务器地址: {addr}")

            # 3. 简单读写测试:建临时表 → 插入 → 查询 → 删表
            with conn.cursor() as cur:
                cur.execute("CREATE TEMP TABLE pgtest_tmp (id serial PRIMARY KEY, name text);")
                cur.execute("INSERT INTO pgtest_tmp (name) VALUES (%s), (%s);", ("hello", "world"))
                cur.execute("SELECT * FROM pgtest_tmp;")
                rows = cur.fetchall()
                print(f"读写测试: {rows}")

            print("✅ 所有测试通过")
    except psycopg.OperationalError as e:
        print(f"❌ 连接失败: {e}")
        print("   排查建议:")
        print("   1. 服务器防火墙/安全组是否放行 5432 端口")
        print("   2. postgresql.conf 中 listen_addresses 是否为 '*'")
        print("   3. pg_hba.conf 是否允许该来源的 md5/scram 认证")
    except psycopg.errors.InsufficientPrivilege as e:
        print(f"❌ 权限不足: {e}")
    except Exception as e:
        print(f"❌ 其他错误: {type(e).__name__}: {e}")


if __name__ == "__main__":
    test_connection()
