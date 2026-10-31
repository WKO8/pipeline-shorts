from types import SimpleNamespace
import pytest
from src.config import AppConfig
from src.models import Transcript,TranscriptSegment,SourceSpan,ClipCandidate
from src.story_editor import StoryEditor,Unit,literal


def transcript():
    return Transcript('pt',segments=[TranscriptSegment(i,i*10,i*10+10,f'Frase {i} completa.') for i in range(40)])


def editor(**kw):
    return StoryEditor(SimpleNamespace(config=AppConfig(**kw),proposals=[],_ask=lambda *a:None))


def test_reading_context_is_larger_than_playable_menu_and_menu_is_legal():
    e=editor(clip_min_seconds=30,clip_max_seconds=60)
    context,menu=e.menu(transcript(),(100,140),80)
    assert context[-1].end-context[0].start>60
    assert menu and all(30<=p['end']-p['start']<=60 for p in menu.values())
    assert any(s.start<min(p['start'] for p in menu.values()) for s in context)


def test_out_of_menu_id_never_becomes_a_source_interval(monkeypatch):
    e=editor();monkeypatch.setattr(e,'ask',lambda *a:dict(option='S999',question='?',answer_quote='Frase 1 completa.',missing_context=''))
    with pytest.raises(ValueError,match='unavailable menu'):
        e.extract(transcript(),(0,40),10)


def test_missing_context_unit_is_recorded_as_unavailable(monkeypatch):
    e=editor();_,menu=e.menu(transcript(),(0,40),10)
    monkeypatch.setattr(e,'ask',lambda *a:dict(option=next(iter(menu)),question='Qual frase?',answer_quote='Frase 1 completa.',missing_context='Falta a resposta.'))
    assert e.extract(transcript(),(0,40),10) is None


def test_same_subject_without_relation_does_not_join(monkeypatch):
    e=editor();u=Unit(SourceSpan(0,30),'','Como?','Resposta A','Resposta A');v=Unit(SourceSpan(90,120),'','Outra?','Resposta B','Resposta B')
    monkeypatch.setattr(e,'ask',lambda *a:dict(relation='apenas_tema',question='Como?',quote_a='Resposta A',quote_b='Resposta B'))
    assert not e.compatible(u,v)


def test_invented_evidence_cannot_approve_targeted_check(monkeypatch):
    e=editor();monkeypatch.setattr(e,'ask',lambda *a:dict(result='ok',evidence_quote='Frase inexistente',reason='Tudo certo'))
    assert e.check('ending','task',{},['Texto original.'])['result']=='incerto'


def test_omission_check_receives_removed_exception(monkeypatch):
    e=editor();seen=[]
    def check(name,task,data,evidence):
        seen.append((name,data));return dict(result='problema' if name=='omission' else 'ok')
    monkeypatch.setattr(e,'check',check)
    verdict=e.review(transcript(),[SourceSpan(0,10),SourceSpan(20,30)],original_spans=[SourceSpan(0,30)])
    assert not verdict['approved']
    assert 'Frase 1 completa.' in next(data for name,data in seen if name=='omission')['original'][0]


def test_hook_is_selected_after_body_and_repeated_without_removal(monkeypatch):
    e=editor(hook_preview_max_seconds=10,clip_max_seconds=60)
    clip=ClipCandidate('a',0,40,source_spans=[SourceSpan(0,40)])
    monkeypatch.setattr(e,'ask',lambda task,data,props:dict(option=next(iter(data['options'])),reason='Chama atenção'))
    hooked=e.hook(transcript(),clip)
    assert hooked.source_spans[0].role=='hook'
    assert hooked.source_spans[1:]==clip.source_spans
    assert hooked.duration==50


def test_public_plan_uses_staged_engine(monkeypatch):
    from src.narrative import NarrativeCurator
    monkeypatch.setattr(StoryEditor,'plan',lambda self,*a:'staged')
    assert NarrativeCurator(AppConfig()).plan_story(transcript(),[(0,40)])=='staged'
