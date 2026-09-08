import math
import pytest
from cxpe.schemas import Cond, Expected, Step, TestPlan
from cxpe.cli import load_plan
from cxpe.engine import RunEngine, run_stream
from cxpe.synth import make_case
from cxpe.telemetry import read_csv


@pytest.mark.parametrize("value", [float("nan"),float("inf"),float("-inf")])
def test_nonfinite_thresholds_are_rejected_and_measurements_are_missing(value):
    with pytest.raises(ValueError):Cond(tag="X",op="==",value=value)
    with pytest.raises(ValueError):Expected(tag="X",op="==",value=1,within_sec=value)
    assert Cond(tag="X",op="!=",value=0).evaluate(value) is None


def test_nonfinite_measurements_result_in_hold():
    rows,_,_=make_case("pass")
    for row in rows:
        if row["t_sec"]>=60:row["CHWS_T_SUP"]=float("nan")
    engine=run_stream(load_plan(),rows)
    assert engine.summary()["overall"]=="HOLD"


@pytest.mark.parametrize("timestamp", [None,float("nan"),float("inf"),"bad",0,-1])
def test_invalid_clock_does_not_advance_engine(timestamp):
    engine=RunEngine(load_plan())
    rows,_,_=make_case("pass");engine.feed(rows[0])
    before=(engine.last_t,len(engine.events),engine.active)
    with pytest.raises(ValueError):engine.feed({**rows[1],"t_sec":timestamp})
    assert (engine.last_t,len(engine.events),engine.active)==before


@pytest.mark.parametrize("csv", ["X\n1\n","t_sec,X,X\n0,1,2\n","t_sec,X\n0,1,2\n", "t_sec,X\nNaN,1\n",
                                  "t_sec,X\n0,1\n0,2\n", "t_sec,X\n1,1\n0,2\n", "t_sec,X\n0,bad\n", "t_sec,X\n"])
def test_malformed_csv_is_rejected_with_explanation(tmp_path,csv):
    p=tmp_path/"input.csv";p.write_text(csv,encoding="utf-8")
    with pytest.raises(ValueError,match="CSV"):read_csv(p)


def test_csv_bom_and_nonfinite_sensor_values(tmp_path):
    p=tmp_path/"input.csv";p.write_text("t_sec,X,Y\n0,NaN,Infinity\n1,,2\n",encoding="utf-8-sig")
    assert read_csv(p)==[{"t_sec":0.0,"X":None,"Y":None},{"t_sec":1.0,"X":None,"Y":2.0}]


def test_empty_plan_cannot_be_a_passing_test():
    with pytest.raises(ValueError):TestPlan(plan_id="EMPTY",title="empty",steps=[])
