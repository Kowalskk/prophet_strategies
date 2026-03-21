import paramiko
import sys

def update_vps():
    hostname = "95.179.243.31"
    username = "root"
    password = "4mZ=hCCo49xD3#DK"
    
    commands = [
        'cd /root/prophet_strategies && git fetch origin main && git reset --hard origin/main',
        'printf "POSTGRES_USER=prophet\nPOSTGRES_PASSWORD=prophet_pass\nPOSTGRES_DB=prophet_db\nPOSTGRES_HOST=postgres\nPOSTGRES_PORT=5432\nREDIS_URL=redis://redis:6379/0\nAPI_SECRET=876ccccd78a8f37cd56acfe843ee41b2ecdadd6f6f14938da438f1553df2b3b0\nCORS_ORIGINS=[\\"*\\", \\"https://dashboard-gamma-azure-85.vercel.app\\"]\nPAPER_TRADING=true\n" > /root/prophet_strategies/engine/.env',
        'cd /root/prophet_strategies && (DOCKER_CONTEXT=default docker-compose down || docker compose down || true)',
        'cd /root/prophet_strategies && (DOCKER_CONTEXT=default docker-compose up -d --build || docker compose up -d --build)'
    ]
    
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname, username=username, password=password)
        
        for cmd in commands:
            print(f"Executing: {cmd}")
            stdin, stdout, stderr = client.exec_command(cmd)
            print(stdout.read().decode())
            print(stderr.read().decode())
        
        client.close()
        print("VPS update complete!")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    update_vps()
