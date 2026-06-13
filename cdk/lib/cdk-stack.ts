import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import { Construct } from 'constructs';
import * as path from 'path';
import * as dotenv from 'dotenv';

// The Anthropic API key lives in the repo-root .env (gitignored), the same
// file used for local runs. Keeping a single source of truth avoids drift.
dotenv.config({ path: path.join(__dirname, '../../.env'), override: true });

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable ${name} (set it in purezen/.env)`);
  }
  return value;
}

export class PureZenStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ── Lambda function (container image: FastAPI via Mangum) ─────────────
    const fn = new lambda.DockerImageFunction(this, 'PureZenApi', {
      code: lambda.DockerImageCode.fromImageAsset(path.join(__dirname, '../..')),
      architecture: lambda.Architecture.X86_64,
      timeout: cdk.Duration.seconds(29),
      memorySize: 1024,
      environment: {
        ENV: 'production',
        // Customer chat + admin LLM both run on Anthropic now.
        ANTHROPIC_API_KEY: requiredEnv('ANTHROPIC_API_KEY'),
        LLM_MODEL: process.env.LLM_MODEL || 'claude-haiku-4-5-20251001',
        // /health/llm stays disabled (404) unless DIAG_TOKEN is set in .env.
        ...(process.env.DIAG_TOKEN ? { DIAG_TOKEN: process.env.DIAG_TOKEN } : {}),
        // Note: AWS_REGION is injected automatically by the Lambda runtime.
      },
    });

    // ── DynamoDB access scoped to the purezen_* tables ───────────────────
    // Tables use both naming styles: purezen_* (underscore) and the
    // purezen-chat-sessions table (hyphen), so cover both prefixes.
    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['dynamodb:*'],
      resources: [
        `arn:aws:dynamodb:${this.region}:${this.account}:table/purezen_*`,
        `arn:aws:dynamodb:${this.region}:${this.account}:table/purezen_*/index/*`,
        `arn:aws:dynamodb:${this.region}:${this.account}:table/purezen-*`,
        `arn:aws:dynamodb:${this.region}:${this.account}:table/purezen-*/index/*`,
      ],
    }));

    // ── API Gateway (proxy). CORS is owned entirely by FastAPI. ──────────
    const api = new apigw.LambdaRestApi(this, 'PureZenGateway', {
      handler: fn,
      proxy: true,
    });

    fn.addPermission('PureZenGatewayInvoke', {
      principal: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      action: 'lambda:InvokeFunction',
      sourceArn: cdk.Stack.of(this).formatArn({
        service: 'execute-api',
        resource: api.restApiId,
        resourceName: '*/*/*',
      }),
    });

    // ── Static frontend hosting (S3 + CloudFront) ────────────────────────
    const siteBucket = new s3.Bucket(this, 'FrontendBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // ── Custom domain for the frontend ───────────────────────────────────
    // Defaults to the production domain so a deploy never silently drops the
    // CNAME (and the matching CORS origin); override via SITE_DOMAIN / CERT_ARN
    // in .env if needed. The cert is the existing *.stephsimmons.dev wildcard
    // in us-east-1. DNS (the Route 53 A-alias) is managed outside this stack.
    const siteDomain = process.env.SITE_DOMAIN || 'purezen.stephsimmons.dev';
    const certArn = process.env.CERT_ARN
      || 'arn:aws:acm:us-east-1:936922781601:certificate/37dec03b-9b1d-44f7-b099-4993542d302c';

    const distribution = new cloudfront.Distribution(this, 'FrontendCDN', {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(siteBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      defaultRootObject: 'index.html',
      domainNames: [siteDomain],
      certificate: acm.Certificate.fromCertificateArn(this, 'SiteCert', certArn),
    });

    // Let FastAPI's CORS accept requests from the custom domain the browser
    // actually loads (not the raw CloudFront hostname).
    fn.addEnvironment('FRONTEND_ORIGIN', `https://${siteDomain}`);

    new s3deploy.BucketDeployment(this, 'FrontendDeployment', {
      sources: [s3deploy.Source.asset(path.join(__dirname, '../../frontend'), {
        exclude: ['.DS_Store', 'patch.py', 'api/**'],
      })],
      destinationBucket: siteBucket,
      distribution,
      distributionPaths: ['/*'],
    });

    // ── Outputs ──────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'ApiUrl', { value: api.url });
    new cdk.CfnOutput(this, 'FrontendUrl', { value: `https://${siteDomain}` });
    new cdk.CfnOutput(this, 'CloudFrontDomain', { value: distribution.distributionDomainName });
  }
}
