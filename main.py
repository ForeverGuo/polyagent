# 向后兼容入口：直接转发到 cli.main
from cli.main import main_cli

if __name__ == "__main__":
    main_cli()
