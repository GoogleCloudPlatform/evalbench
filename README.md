# evalbench

To run EvalBench on local vm:

Clone the repo:
```
git clone git@github.com:GoogleCloudPlatform/evalbench.git
```

Create a virtual environment:
```
cd evalbench
python3 -m venv venv
source venv/bin/activate
```
Install the dependencies:
```
pip install -r requirements.txt
```

Due to proto conflict between google-cloud-alloydb-connector and googleapis-common-protos, you need to force install common-protos:
```
pip install --force-reinstall googleapis-common-protos==1.64.0
```
Configure your DB password:
```
export EVAL_DB_PASSWORD=#######
```
Then run eval:

```
cd evalbench
./run.sh
```
