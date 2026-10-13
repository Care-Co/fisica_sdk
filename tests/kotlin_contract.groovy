import com.carenco.fisica.*
import groovy.json.JsonSlurper
import java.security.MessageDigest
import java.nio.ByteBuffer
import java.nio.ByteOrder
byte[] fromHex(String s) { s.decodeHex() }
def core=new FisicaCore(args[0],{System.err.println(it)} as kotlin.jvm.functions.Function1)
def parser=new JsonSlurper()
parser.parse(new File(args[1],'sessions.json')).each { v ->
    def session=new FisicaSession(core)
    try {
        v.steps.each { step ->
            long now=step.now_ms
            if(step.containsKey('action')) session.action(step.action as int,step.value as long,now)
            else if(step.containsKey('feed_hex')) session.feed(fromHex(step.feed_hex),now)
            else if(step.containsKey('command')) session.command(step.command,0d,new byte[0],now)
            else session.tick(now)
            def actual=session.drain().collect { e -> [(0..<5).collect { e.metadata[it] },e.payload.encodeHex().toString()].flatten() }
            assert actual==step.events:v.id
        }
    } finally { session.close() }
}
parser.parse(new File(args[1],'legacy-processing.json')).each { v ->
    def m=core.decode(fromHex(v.packet_hex));assert m.weight==v.weight
    def data=ByteBuffer.allocate(m.display.length*8).order(ByteOrder.LITTLE_ENDIAN)
    m.display.each { data.putDouble(it) }
    assert MessageDigest.getInstance('SHA-256').digest(data.array()).encodeHex().toString()==v.display_sha256
}
def grid=new double[841];grid[4*29+3]=grid[4*29+20]=100
def matrices=new double[841*12];for(int i=0;i<12;i++) System.arraycopy(grid,0,matrices,i*841,841)
def weights=new double[12];Arrays.fill(weights,70.5d)
assert core.analyze(matrices,weights,29,29).toList()==[70.5d,1d,1d,1d,1d]
println 'Kotlin/JNI shared session and processing vectors passed'
