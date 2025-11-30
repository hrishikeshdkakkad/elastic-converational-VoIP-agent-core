# Deployment Guide

Production deployment options and configurations.

## Deployment Options

| Option | Best For | Complexity | Cost |
|--------|----------|------------|------|
| Docker Compose | Small deployments, single server | Low | $ |
| AWS ECS (Terraform) | Production, multi-region | Medium | $$ |
| Kubernetes | Large scale, existing K8s | High | $$$ |

---

## Docker Compose Production

### Basic Production Setup

1. **Update `.env` for production:**

```bash
ENVIRONMENT=production
LOG_LEVEL=WARNING
BASE_URL=https://voice-ai.yourdomain.com

# Strong passwords
POSTGRES_PASSWORD=<strong-random-password>
REDIS_PASSWORD=<strong-random-password>

# Production API keys
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxx
TWILIO_PHONE_NUMBER=+1234567890
GEMINI_API_KEY=AIzaSyxxxxxxxx
```

2. **Scale workers:**

```yaml
# docker-compose.yml
worker:
  deploy:
    replicas: 5
```

3. **Add reverse proxy (nginx):**

```nginx
upstream api_backend {
    server api:8000;
}

server {
    listen 443 ssl;
    server_name voice-ai.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://api_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

4. **Deploy:**

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

---

## AWS ECS Deployment (Terraform)

The included Terraform modules deploy a production-ready infrastructure on AWS.

### Architecture

```
                      +------------------+
                      |      Twilio      |
                      +--------+---------+
                               |
                      +--------v---------+
                      |    Route 53      |
                      +--------+---------+
                               |
               +---------------v---------------+
               |    AWS Global Accelerator     |
               +---------------+---------------+
                               |
         +---------------------+---------------------+
         v                                           v
+----------------+                         +----------------+
|   us-east-1    |                         |   us-west-2    |
|   (PRIMARY)    |                         |   (STANDBY)    |
|                |                         |                |
|  ALB -> ECS    |                         |  Cold Standby  |
|  RDS           |                         |  (VPC only)    |
|  ElastiCache   |                         |                |
+----------------+                         +----------------+
```

### Estimated Monthly Cost: ~$155

| Service | Specification | Cost |
|---------|--------------|------|
| ECS Fargate - API | 2 tasks @ 0.5 vCPU / 1GB | ~$36 |
| ECS Fargate - Worker (Spot) | 2 tasks @ 0.25 vCPU / 512MB | ~$5 |
| Application Load Balancer | 1 ALB | ~$18 |
| RDS PostgreSQL | db.t3.micro, 20GB | ~$16 |
| ElastiCache Redis | cache.t3.micro | ~$12 |
| NAT Gateway | Single (1 AZ) | ~$32 |
| Global Accelerator | Base + data | ~$20 |
| Route 53 + CloudWatch | Misc | ~$16 |

### Prerequisites

1. AWS CLI configured with appropriate credentials
2. Terraform >= 1.5.0
3. Domain name with Route53 hosted zone
4. ACM certificates in both regions

### Deploy Primary Region

```bash
cd terraform/environments/production/us-east-1

# Configure variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars

# Set sensitive variables
export TF_VAR_db_password="your-secure-password"
export TF_VAR_twilio_account_sid="ACxxxxxxxxxx"
export TF_VAR_twilio_auth_token="your-auth-token"
export TF_VAR_twilio_phone_number="+1234567890"
export TF_VAR_gemini_api_key="your-gemini-api-key"

# Deploy
terraform init
terraform plan
terraform apply
```

### Deploy DR Region (Cold Standby)

```bash
cd terraform/environments/production/us-west-2

cp terraform.tfvars.example terraform.tfvars
terraform init
terraform apply
```

### Push Docker Images

```bash
# Login to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin \
  <account-id>.dkr.ecr.us-east-1.amazonaws.com

# Build and push
docker build -f docker/Dockerfile.api -t voice-ai/api .
docker tag voice-ai/api:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/voice-ai/api:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/voice-ai/api:latest

docker build -f docker/Dockerfile.worker -t voice-ai/worker .
docker tag voice-ai/worker:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/voice-ai/worker:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/voice-ai/worker:latest
```

### Disaster Recovery

**Failover to DR region:**

```bash
./terraform/scripts/failover/activate_standby.sh
```

**Return to primary:**

```bash
./terraform/scripts/failover/deactivate_standby.sh
```

**RTO/RPO:**
- RTO: ~15 minutes
- RPO: Up to 24 hours (last daily backup)

---

## Temporal Cloud

For managed Temporal, use Temporal Cloud instead of self-hosted.

### Configuration

```bash
# .env
TEMPORAL_HOST=your-namespace.tmprl.cloud
TEMPORAL_PORT=7233
TEMPORAL_NAMESPACE=your-namespace
TEMPORAL_TLS_ENABLED=true
TEMPORAL_API_KEY=your-temporal-api-key
```

### Benefits

- No Temporal infrastructure to manage
- Built-in HA and disaster recovery
- Automatic upgrades
- Web UI included

---

## Scaling Guidelines

### Horizontal Scaling

| Component | Scale By | Considerations |
|-----------|----------|----------------|
| API | Replicas | Stateless, add behind load balancer |
| Workers | Replicas | Each handles concurrent activities |
| PostgreSQL | Vertical/Read replicas | Connection pooling critical |
| Redis | Cluster mode | For session distribution |

### Recommended Resources

**Small (< 100 concurrent calls):**
- API: 1 replica, 1 CPU, 2GB RAM
- Worker: 2 replicas, 1 CPU, 2GB RAM each
- PostgreSQL: 2 CPU, 4GB RAM
- Redis: 1 CPU, 1GB RAM

**Medium (100-500 concurrent calls):**
- API: 2 replicas, 2 CPU, 4GB RAM each
- Worker: 5 replicas, 2 CPU, 4GB RAM each
- PostgreSQL: 4 CPU, 8GB RAM
- Redis: 2 CPU, 2GB RAM

**Large (500+ concurrent calls):**
- API: 3+ replicas with auto-scaling
- Worker: 10+ replicas with auto-scaling
- PostgreSQL: 8+ CPU, 16GB+ RAM, read replicas
- Redis: Cluster mode

### Auto-Scaling

**ECS Auto-Scaling (already in Terraform):**

```hcl
resource "aws_appautoscaling_target" "api" {
  max_capacity       = 10
  min_capacity       = 2
  resource_id        = "service/${cluster}/${service}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "cpu-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.api.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value = 70.0
  }
}
```

---

## Security

### Network Security

- VPC with private subnets for databases
- Security groups restricting access
- NAT Gateway for outbound traffic
- TLS everywhere

### Secrets Management

**AWS Secrets Manager (Terraform):**

```hcl
resource "aws_secretsmanager_secret" "api_keys" {
  name = "voice-ai/api-keys"
}
```

**Application access:**

```python
import boto3

def get_secret(secret_name):
    client = boto3.client('secretsmanager')
    response = client.get_secret_value(SecretId=secret_name)
    return json.loads(response['SecretString'])
```

### API Authentication

Implement JWT authentication for production:

```python
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer

security = HTTPBearer()

async def verify_token(credentials = Depends(security)):
    token = credentials.credentials
    # Verify JWT token
    if not valid:
        raise HTTPException(status_code=401)
```

---

## Monitoring

### CloudWatch (AWS)

Pre-configured dashboards for:
- ECS service metrics
- ALB latency and errors
- RDS performance
- Redis metrics

### Alerts

```bash
# Subscribe to alerts
aws sns subscribe \
    --topic-arn arn:aws:sns:us-east-1:xxx:voice-ai-alerts \
    --protocol email \
    --notification-endpoint your-email@example.com
```

### Key Metrics

| Metric | Target | Action if Exceeded |
|--------|--------|-------------------|
| API Latency (p95) | < 100ms | Scale API, check DB |
| Error Rate (5xx) | < 1% | Check logs, rollback |
| ECS CPU | < 70% | Scale up |
| DB Connections | < 80% | Increase pool or scale |

### Health Checks

ECS health checks are configured to use:
- `/health/live` - Liveness
- `/health/ready` - Readiness

---

## CI/CD

### GitHub Actions Example

```yaml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: us-east-1

      - name: Login to ECR
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build and Push
        run: |
          docker build -f docker/Dockerfile.api -t $ECR_REGISTRY/voice-ai/api:${{ github.sha }} .
          docker push $ECR_REGISTRY/voice-ai/api:${{ github.sha }}

      - name: Deploy to ECS
        run: |
          aws ecs update-service --cluster voice-ai --service api --force-new-deployment
```

### Terraform CI/CD (Optional)

The Terraform modules include optional CodePipeline configuration:

```hcl
# terraform.tfvars
enable_cicd           = true
github_connection_arn = "arn:aws:codestar-connections:..."
github_repo           = "your-org/voice-ai"
github_branch         = "main"
```

---

## Rollback Procedures

### ECS Rollback

```bash
# List recent deployments
aws ecs describe-services --cluster voice-ai --services api

# Force deployment of previous task definition
aws ecs update-service \
  --cluster voice-ai \
  --service api \
  --task-definition voice-ai-api:PREVIOUS_VERSION \
  --force-new-deployment
```

### Database Rollback

```bash
# Rollback one migration
docker compose exec api alembic downgrade -1

# Rollback to specific version
docker compose exec api alembic downgrade abc123
```

### Terraform Rollback

```bash
# Rollback to previous state
terraform apply -target=module.ecs -var="image_tag=previous-version"
```

---

## Backup and Recovery

### Database Backups

**RDS (automatic):**
- Daily snapshots retained 7 days
- Point-in-time recovery enabled

**Manual backup:**
```bash
pg_dump -h localhost -p 5433 -U temporal -d voice_ai > backup.sql
```

### Recovery

```bash
# Restore from snapshot (AWS)
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier voice-ai-restored \
  --db-snapshot-identifier voice-ai-snapshot-xxx

# Manual restore
psql -h localhost -p 5433 -U temporal -d voice_ai < backup.sql
```
