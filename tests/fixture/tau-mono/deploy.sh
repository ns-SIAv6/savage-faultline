export DEPLOY_TARGET=prod
aws s3 sync s3://savage-deploy-artifacts .
