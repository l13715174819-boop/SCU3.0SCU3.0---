import sys
import webview

print("1. 导入 webview 成功")
print(f"2. webview 版本: {webview.__version__ if hasattr(webview, '__version__') else 'unknown'}")

# 尝试获取后端
backend = webview.platforms.current if hasattr(webview, 'platforms') else None
print(f"3. 当前平台后端: {backend}")

# 尝试创建窗口
try:
    print("4. 尝试创建窗口...")
    window = webview.create_window("测试窗口", "about:blank", width=400, height=300)
    print("5. 窗口创建成功")
    
    print("6. 尝试启动 webview...")
    webview.start(debug=False)
    print("7. webview 已启动并退出")
except Exception as e:
    print(f"❌ 错误: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
