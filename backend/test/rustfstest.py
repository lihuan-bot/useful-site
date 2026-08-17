"""
RustFS Python SDK 使用案例测试
================================
参考文档: https://docs.rustfs.com/zh/developer/sdk/python

RustFS 不提供第一方 SDK, 但与 S3 兼容, 因此使用官方 AWS SDK for Python (boto3)。

测试覆盖文档中的全部功能:
  1. 连接 RustFS 并 list_buckets
  2. 创建存储桶 (create_bucket)
  3. 上传文件 (upload_file)
  4. 下载文件 (download_file)
  5. 列出对象 (list_objects_v2)
  6. 生成预签名 GET URL (并用它实际下载验证)
  7. 生成预签名 PUT URL (并用它实际上传验证)
  8. 分片上传 (multipart upload, 针对大文件)
  9. 文件夹上传 (Key 前缀模拟目录, 含空文件夹/嵌套子文件夹)
  10. 删除对象和存储桶 (delete_object / delete_bucket)

运行: uv run python rustfstest.py
"""

import os
import sys
import tempfile
import traceback
import urllib.request

import boto3
from botocore.client import Config

# ---------------- 连接配置 ----------------
ENDPOINT = "http://127.0.0.1:9000"  # S3 API 端口是 9000
ACCESS_KEY = "123456"
SECRET_KEY = "123456"
REGION = "us-east-1"  # RustFS 默认区域
BUCKET = "rustfs-python-sdk-test1"  # 存储桶名: 只能小写字母/数字/连字符, 3-63 字符

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    region_name=REGION,
    config=Config(
        signature_version="s3v4",  # RustFS 支持 v4 签名
        s3={"addressing_style": "path"},  # RustFS 默认路径风格 URL
    ),
)


# ---------------- 迷你测试框架 ----------------
def _run(test_name, fn):
    print(f"\n===== {test_name} =====")
    try:
        fn()
    except Exception:
        traceback.print_exc()
        print(f"✗ FAIL: {test_name}")
        return False
    print(f"✓ PASS: {test_name}")
    return True


def _cleanup_bucket(bucket: str):
    """删除桶内全部对象后删除桶 (可能包含未完成的分片上传)"""
    try:
        # 中止未完成的分片上传
        for mp in s3.list_multipart_uploads(Bucket=bucket).get("Uploads", []):
            s3.abort_multipart_upload(
                Bucket=bucket, Key=mp["Key"], UploadId=mp["UploadId"]
            )
        # 删除全部对象
        for obj in s3.list_objects_v2(Bucket=bucket).get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=obj["Key"])
        s3.delete_bucket(Bucket=bucket)
        print(f"  [cleanup] 已删除旧桶 {bucket}")
    except Exception:
        pass  # 桶不存在等情况直接忽略


# ---------------- 1. 连接测试: list_buckets ----------------
def test_list_buckets():
    response = s3.list_buckets()
    names = [b["Name"] for b in response["Buckets"]]
    print(f"  已有存储桶: {names or '(无)'}")
    assert isinstance(names, list)


# ---------------- 2. 创建存储桶 ----------------
def test_create_bucket():
    _cleanup_bucket(BUCKET)  # 先清掉可能残留的旧桶, 保证测试可重复运行
    try:
        s3.create_bucket(Bucket=BUCKET)
        print(f"  存储桶 {BUCKET} 创建成功")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print(f"  存储桶 {BUCKET} 已存在 (BucketAlreadyOwnedByYou)")

    names = [b["Name"] for b in s3.list_buckets()["Buckets"]]
    assert BUCKET in names, "list_buckets 中找不到新创建的桶"


# ---------------- 3. 上传文件 ----------------
def test_upload_file():
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("Hello RustFS! 你好, RustFS!\n")
        local_path = f.name

    try:
        s3.upload_file(local_path, BUCKET, "hello.txt")
        print("  文件上传成功: hello.txt")
    finally:
        os.unlink(local_path)

    # 用 head_object 验证对象确实存在
    head = s3.head_object(Bucket=BUCKET, Key="hello.txt")
    assert head["ContentLength"] > 0
    print(f"  head_object 验证: 大小 {head['ContentLength']} 字节")


# ---------------- 4. 下载文件 ----------------
def test_download_file():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        local_path = f.name
    try:
        s3.download_file(BUCKET, "hello.txt", local_path)
        content = open(local_path, encoding="utf-8").read()
        print(f"  文件下载成功, 内容: {content!r}")
        assert "Hello RustFS" in content
    finally:
        os.unlink(local_path)


# ---------------- 5. 列出对象 ----------------
def test_list_objects():
    response = s3.list_objects_v2(Bucket=BUCKET)
    objs = response.get("Contents", [])
    print(f"  桶 {BUCKET} 中的对象:")
    for obj in objs:
        print(f"    - {obj['Key']} ({obj['Size']} bytes)")
    assert any(o["Key"] == "hello.txt" for o in objs)


# ---------------- 6. 生成预签名 GET URL ----------------
def test_presigned_get_url():
    url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": BUCKET, "Key": "hello.txt"},
        ExpiresIn=600,  # 10 分钟有效期
    )
    print(f"  预签名 GET URL: {url}")

    # 实际用该 URL 下载验证
    with urllib.request.urlopen(url, timeout=30) as resp:
        content = resp.read().decode("utf-8")
    assert "Hello RustFS" in content
    print(f"  通过 URL 下载成功: {content!r}")


# ---------------- 7. 生成预签名 PUT URL ----------------
def test_presigned_put_url():
    url = s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={"Bucket": BUCKET, "Key": "upload-by-url.txt"},
        ExpiresIn=600,
    )
    print(f"  预签名 PUT URL: {url}")

    # 实际用该 URL 上传验证 (等价于 curl -X PUT --upload-file ...)
    data = b"Uploaded via presigned PUT URL\n"
    req = urllib.request.Request(
        url, data=data, method="PUT",
        headers={"Content-Type": "text/plain"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        assert resp.status == 200
    print("  通过 URL 上传成功")

    # 下载回来验证内容一致
    obj = s3.get_object(Bucket=BUCKET, Key="upload-by-url.txt")
    assert obj["Body"].read() == data
    print(f"  内容校验一致: {data!r}")


# ---------------- 8. 分片上传 (multipart upload) ----------------
def test_multipart_upload():
    key = "largefile.bin"
    part_size = 5 * 1024 * 1024  # 5 MB (文档推荐 >10MB 文件使用分片上传)
    total_size = 12 * 1024 * 1024  # 12 MB → 3 个分片

    # 生成 12MB 测试文件
    with tempfile.NamedTemporaryFile(delete=False) as f:
        local_path = f.name
        f.write(os.urandom(total_size))
    print(f"  测试文件: {total_size // 1024 // 1024} MB")

    # 1. 开始分片上传
    response = s3.create_multipart_upload(Bucket=BUCKET, Key=key)
    upload_id = response["UploadId"]
    parts = []

    try:
        with open(local_path, "rb") as f:
            part_number = 1
            while True:
                data = f.read(part_size)
                if not data:
                    break

                part = s3.upload_part(
                    Bucket=BUCKET,
                    Key=key,
                    PartNumber=part_number,
                    UploadId=upload_id,
                    Body=data,
                )
                parts.append({"ETag": part["ETag"], "PartNumber": part_number})
                print(f"  已上传分片 {part_number} ({len(data)} bytes)")
                part_number += 1

        # 2. 完成分片上传
        s3.complete_multipart_upload(
            Bucket=BUCKET,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
        print("  分片上传完成")

    except Exception:
        # 失败时中止上传
        s3.abort_multipart_upload(Bucket=BUCKET, Key=key, UploadId=upload_id)
        os.unlink(local_path)
        raise

    # 验证: 大小一致 + 内容一致
    head = s3.head_object(Bucket=BUCKET, Key=key)
    assert head["ContentLength"] == total_size, (
        f"大小不一致: {head['ContentLength']} != {total_size}"
    )

    with tempfile.NamedTemporaryFile(delete=False) as f:
        dl_path = f.name
    s3.download_file(BUCKET, key, dl_path)
    with open(dl_path, "rb") as f, open(local_path, "rb") as g:
        assert f.read() == g.read(), "下载内容与上传内容不一致"
    os.unlink(local_path)
    os.unlink(dl_path)
    print(f"  大小和内容校验通过 ({total_size} bytes)")


# ---------------- 9. 文件夹上传 ----------------
# S3/RustFS 没有真正的"文件夹": 桶是扁平结构, 目录靠对象 Key 中的 "/" 前缀模拟。
#  - 上传 Key 为 "docs/report.txt" 的对象 = 把文件放进 docs 文件夹
#  - 上传 Key 以 "/" 结尾的空对象 = 创建一个空文件夹 (控制台可见)
FOLDER_KEYS = [
    "docs/report.txt",
    "docs/2026/notes.txt",  # 嵌套子文件夹 docs/2026/
]


def test_folder_upload():
    # 1. 创建空文件夹 "docs" 和空子文件夹 "docs/2026"
    #    (只有文件没有这个也行, 但空文件夹必须显式创建一个 "/" 结尾的空对象)
    s3.put_object(Bucket=BUCKET, Key="docs/", Body=b"")
    print("  已创建空文件夹: docs/")

    # 2. 上传文件到 docs/ 下
    content1 = "docs 目录下的报告\n".encode("utf-8")
    s3.put_object(Bucket=BUCKET, Key="docs/report.txt", Body=content1)
    print("  已上传: docs/report.txt")

    # 3. 上传文件到嵌套子文件夹 docs/2026/ 下
    content2 = "docs/2026 目录下的笔记\n".encode("utf-8")
    s3.put_object(Bucket=BUCKET, Key="docs/2026/notes.txt", Body=content2)
    print("  已上传: docs/2026/notes.txt")

    # 4. 用 Delimiter='/' 模拟文件夹视图列目录
    #    - Contents        => 当前目录下的文件
    #    - CommonPrefixes  => 当前目录下的子文件夹
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix="docs/", Delimiter="/")
    all_keys = [o["Key"] for o in resp.get("Contents", [])]
    # 注意: 空文件夹标记 "docs/" 本身也会出现在 Contents 里 (与 AWS S3 行为一致),
    # 需要自己过滤掉以 "/" 结尾的目录标记, 剩下的才是真正的文件
    files = [k for k in all_keys if not k.endswith("/")]
    subdirs = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
    print(f"  Contents 全部 Key: {all_keys}")
    print(f"  docs/ 下的文件:   {files}")
    print(f"  docs/ 下的子目录: {subdirs}")
    assert files == ["docs/report.txt"], "docs/ 下文件列表不对"
    assert subdirs == ["docs/2026/"], "docs/ 下子目录列表不对"

    # 5. 从文件夹路径下载回来验证内容一致
    obj = s3.get_object(Bucket=BUCKET, Key="docs/report.txt")
    assert obj["Body"].read() == content1
    obj = s3.get_object(Bucket=BUCKET, Key="docs/2026/notes.txt")
    assert obj["Body"].read() == content2
    print("  两个文件内容校验一致")

    # 6. 给文件夹内的文件生成预签名 URL (说明路径风格对带 / 的 Key 同样生效)
    url = s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": BUCKET, "Key": "docs/2026/notes.txt"},
        ExpiresIn=600,
    )
    with urllib.request.urlopen(url, timeout=30) as r:
        assert r.read() == content2
    print("  文件夹内文件预签名 URL 下载成功")


# ---------------- 10. 删除对象和存储桶 ----------------
def test_delete():
    s3.delete_object(Bucket=BUCKET, Key="hello.txt")
    print("  对象已删除: hello.txt")
    s3.delete_object(Bucket=BUCKET, Key="upload-by-url.txt")
    print("  对象已删除: upload-by-url.txt")
    s3.delete_object(Bucket=BUCKET, Key="largefile.bin")
    print("  对象已删除: largefile.bin")

    for key in FOLDER_KEYS + ["docs/"]:
        s3.delete_object(Bucket=BUCKET, Key=key)
        print(f"  对象已删除: {key}")

    s3.delete_bucket(Bucket=BUCKET)
    print(f"  存储桶已删除: {BUCKET}")

    names = [b["Name"] for b in s3.list_buckets()["Buckets"]]
    assert BUCKET not in names


# ---------------- 主流程 ----------------
def main():
    print("RustFS Python SDK (boto3) 使用案例测试")
    print(f"Endpoint: {ENDPOINT}")
    print(f"Bucket:   {BUCKET}\n")

    results = [
        _run("1. 连接 RustFS (list_buckets)", test_list_buckets),
        _run("2. 创建存储桶 (create_bucket)", test_create_bucket),
        _run("3. 上传文件 (upload_file)", test_upload_file),
        _run("4. 下载文件 (download_file)", test_download_file),
        _run("5. 列出对象 (list_objects_v2)", test_list_objects),
        _run("6. 预签名 GET URL", test_presigned_get_url),
        _run("7. 预签名 PUT URL", test_presigned_put_url),
        _run("8. 分片上传 (multipart upload)", test_multipart_upload),
        _run("9. 文件夹上传 (Key 前缀模拟目录)", test_folder_upload),
        # _run("10. 删除对象和存储桶", test_delete),
    ]

    passed = sum(results)
    print(f"\n{'=' * 40}")
    print(f"结果: {passed}/{len(results)} 项测试通过")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
