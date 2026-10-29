#!/usr/bin/env python3
"""Read-only export of the live Image Builder pipeline and its component documents.

No AWS writes. Capture as reviewable JSON before reproducing the GPU AMI recipe.
"""
import argparse
import json
import subprocess
from pathlib import Path

def aws(region, *args):
    return json.loads(subprocess.check_output(["aws","imagebuilder",*args,"--region",region,"--output","json"],text=True))

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--pipeline-arn",required=True)
    p.add_argument("--region",default="us-east-1")
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    pipeline=aws(a.region,"get-image-pipeline","--image-pipeline-arn",a.pipeline_arn)["imagePipeline"]
    recipe_arn=pipeline.get("imageRecipeArn")
    if not recipe_arn:
        raise SystemExit("Pipeline does not reference an EC2 Image Recipe")
    recipe=aws(a.region,"get-image-recipe","--image-recipe-arn",recipe_arn)["imageRecipe"]
    components=[]
    for item in recipe["components"]:
        arn=item["componentArn"]
        component=aws(a.region,"get-component","--component-build-version-arn",arn)["component"]
        components.append({"arn":arn,"name":component.get("name"),
                           "version":component.get("version"),
                           "platform":component.get("platform"),
                           "data":component.get("data"),
                           "parameters":item.get("parameters",[])})
    infra=aws(a.region,"get-infrastructure-configuration",
              "--infrastructure-configuration-arn",pipeline["infrastructureConfigurationArn"])["infrastructureConfiguration"]
    distribution=None
    if pipeline.get("distributionConfigurationArn"):
        distribution=aws(a.region,"get-distribution-configuration",
                         "--distribution-configuration-arn",pipeline["distributionConfigurationArn"])["distributionConfiguration"]
    result={"pipeline":pipeline,"recipe":recipe,"components":components,
            "infrastructure":infra,"distribution":distribution}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2,default=str)+"\n")
    print("Read-only Image Builder inventory:",a.output)
    print("Recipe:",recipe.get("name"),recipe.get("version"))
    print("Components:",*[f"{c['name']}:{c['version']}" for c in components],sep="\n  ")
    print("Review exported document before sharing; it may contain network and IAM details.")

if __name__=="__main__":
    main()
